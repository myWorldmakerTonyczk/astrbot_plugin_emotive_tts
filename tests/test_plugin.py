import asyncio
from types import SimpleNamespace

import astrbot.api.message_components as Comp
import pytest
from plugin_under_test.main import EmotiveTTS
from plugin_under_test.src.events import DONE, SNAPSHOT, TOOL_BUSY, TOOL_SENT
from plugin_under_test.src.queue_manager import WorkGate
from plugin_under_test.src.tools.tool import _send_voice


class Event:
    def __init__(self, umo="bot:FriendMessage:1", group="", fail=False):
        self.unified_msg_origin = umo
        self.group, self.fail = group, fail
        self.extra, self.sent = {}, []
        self.result = SimpleNamespace(
            chain=[Comp.Plain("**你好**")],
            is_llm_result=lambda: True,
            result_content_type="LLM_RESULT",
        )

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_group_id(self):
        return self.group

    def get_message_str(self):
        return "用户本次输入"

    def get_result(self):
        return self.result

    def chain_result(self, chain):
        return SimpleNamespace(chain=chain)

    async def send(self, result):
        if self.fail:
            raise RuntimeError("secret should not leak")
        self.sent.append(result)


@pytest.fixture
def plugin(tmp_path):
    obj = object.__new__(EmotiveTTS)
    obj.context = SimpleNamespace()
    obj.config = {"send_text_with_voice": False, "auto_emotion": False}
    obj.closing, obj.cleaner = False, None
    obj.jobs, obj.gate, obj.data_dir = set(), WorkGate(), tmp_path

    async def synth(config, text, instruction):
        return b"test audio"

    async def close():
        pass

    obj.http = SimpleNamespace(synthesize=synth, close=close)
    return obj


@pytest.mark.asyncio
async def test_capture_isolation_and_no_recapture(plugin):
    a, b = Event(), Event("bot:FriendMessage:2")
    req = SimpleNamespace(contexts=[{"role": "user", "content": "甲的历史"}], system_prompt="角色")
    await plugin.capture(a, req)
    req.contexts[0]["content"] = "乙的历史"
    await plugin.capture(b, req)
    await plugin.capture(a, req)
    assert a.get_extra(SNAPSHOT)["history"][0]["content"] == "甲的历史"
    assert b.get_extra(SNAPSHOT)["history"][0]["content"] == "乙的历史"


@pytest.mark.asyncio
async def test_success_no_duplicates(plugin):
    event = Event()
    await plugin.decorate(event)
    assert len(event.sent) == 1 and isinstance(event.sent[0].chain[0], Comp.Record)
    assert not event.result.chain and event.get_extra(DONE)
    await plugin.decorate(event)
    assert len(event.sent) == 1 and not plugin.jobs and not plugin.gate.slots


@pytest.mark.asyncio
async def test_send_failure_keeps_text(plugin):
    event = Event(fail=True)
    await plugin.decorate(event)
    assert event.result.chain[0].text == "**你好**" and not plugin.jobs


@pytest.mark.asyncio
async def test_synthesis_failure_keeps_text(plugin):
    async def fail(*args):
        raise RuntimeError("key=secret")

    plugin.http.synthesize = fail
    event = Event()
    await plugin.decorate(event)
    assert event.result.chain and not event.sent


@pytest.mark.asyncio
async def test_bad_analysis_falls_back(plugin):
    plugin.config.update(auto_emotion=True, fallback_instruction="<bad>")

    async def bad(*args):
        raise RuntimeError("secret")

    plugin.context = SimpleNamespace(get_using_provider_async=bad)
    assert (await plugin.instruction(Event(), "你好")).startswith("用普通话")


@pytest.mark.asyncio
async def test_external_analysis(plugin):
    plugin.config.update(auto_emotion=True, analysis_mode="external")

    async def analyze(*args):
        return '{"instruction":"用普通话，先轻快，再关心。"}'

    plugin.http.analyze_external = analyze
    assert await plugin.instruction(Event(), "你好") == "用普通话，先轻快，再关心。"


@pytest.mark.asyncio
async def test_streaming_and_existing_audio_skip(plugin):
    event = Event()
    event.result.result_content_type = "STREAMING_FINISH"
    await plugin.decorate(event)
    assert not event.sent
    event.result.result_content_type = "LLM_RESULT"
    event.result.chain.append(Comp.Record(file="existing"))
    await plugin.decorate(event)
    assert not event.sent


@pytest.mark.asyncio
async def test_gates_and_keep_text_setting(plugin):
    event = Event()
    plugin.config.update(umo_whitelist=["other"])
    await plugin.decorate(event)
    assert not event.sent
    plugin.config.update(umo_whitelist=[], trigger_probability=0)
    await plugin.decorate(event)
    assert not event.sent
    plugin.config.update(trigger_probability=1, max_text_chars=1)
    await plugin.decorate(event)
    assert not event.sent
    plugin.config.update(max_text_chars=600, send_text_with_voice=True)
    await plugin.decorate(event)
    assert len(event.sent) == 1 and event.result.chain


@pytest.mark.asyncio
async def test_unload_cancels_jobs(plugin):
    task = asyncio.create_task(asyncio.sleep(100))
    plugin.jobs.add(task)
    await plugin.terminate()
    assert task.cancelled() and plugin.closing


@pytest.mark.asyncio
async def test_tool_manual_bypasses_analysis_and_automatic_repeat(plugin):
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("manual must not analyze tone")

    async def synth(config, text, instruction):
        calls.append((text, instruction))
        return b"audio"

    plugin.instruction = forbidden
    plugin.http.synthesize = synth
    event = Event()
    result = await plugin.send_voice_manual(event, "**你好呀**", "先俏皮，再温柔。")
    assert "成功" in result and calls == [("你好呀", "先俏皮，再温柔。")]
    assert event.get_extra(TOOL_SENT) and not event.get_extra(TOOL_BUSY)
    await plugin.send_voice_manual(event, "不应再发", "温柔。")
    await plugin.send_voice_auto(event, "也不能再用自动工具发送")
    await plugin.decorate(event)
    assert len(event.sent) == 1 and event.result.chain and not plugin.jobs


@pytest.mark.asyncio
async def test_tool_auto_uses_bounded_snapshot_even_when_auto_emotion_off(plugin):
    import json

    plugin.config.update(context_messages=2, analysis_mode="external", auto_reply_voice=False)
    payloads, voices = [], []

    async def analyze(config, prompt, payload):
        payloads.append(json.loads(payload))
        return '{"instruction":"先低落，再带一点期待。"}'

    async def synth(config, text, instruction):
        voices.append(instruction)
        return b"audio"

    plugin.http.analyze_external = analyze
    plugin.http.synthesize = synth
    event = Event()
    req = SimpleNamespace(
        contexts=[{"role": "user", "content": x} for x in ["最早", "上一条", "最近"]],
        system_prompt="角色",
    )
    await plugin.capture(event, req)
    assert "成功" in await plugin.send_voice_auto(event, "那你明天还来吗？")
    # Tool default is five, independently of the automatic reply's configured two.
    assert [x["content"] for x in payloads[0]["history"]] == ["最早", "上一条", "最近"]
    assert payloads[0]["current_user"] == "用户本次输入"
    assert payloads[0]["reply"] == "那你明天还来吗？"
    assert voices == ["先低落，再带一点期待。"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,mode,instruction",
    [
        ("", "auto", ""),
        ("你好", "bad", ""),
        ("你好", "manual", ""),
        ("你好", "manual", "<bad>"),
        ("你好", "manual", "长" * 51),
        ("你好", "auto", "温柔"),
        (None, "auto", ""),
        ("长" * 601, "auto", ""),
    ],
)
async def test_tool_rejects_bad_parameters(plugin, text, mode, instruction):
    event = Event()
    result = await _send_voice(plugin, event, text, mode, instruction)
    assert "未发送" in result and not event.sent and not plugin.jobs


@pytest.mark.asyncio
async def test_tool_permissions_and_auto_switch(plugin):
    for config in [
        {"tool_enabled": False},
        {"enabled": False},
        {"private_enabled": False},
        {"umo_whitelist": ["other"]},
    ]:
        before = dict(plugin.config)
        plugin.config.update(config)
        event = Event()
        assert "未启用" in await plugin.send_voice_auto(event, "你好")
        assert not event.sent
        plugin.config = before
    plugin.config["auto_reply_voice"] = False
    event = Event()
    await plugin.decorate(event)
    assert not event.sent


@pytest.mark.asyncio
async def test_tool_failure_allows_retry_and_preserves_text(plugin):
    event = Event(fail=True)
    assert "失败" in await plugin.send_voice_manual(event, "你好", "温柔。")
    assert not event.get_extra(TOOL_SENT) and not event.get_extra(TOOL_BUSY)
    assert event.result.chain and not plugin.jobs
    event.fail = False
    assert "成功" in await plugin.send_voice_manual(event, "你好", "温柔。")


@pytest.mark.asyncio
async def test_tool_busy_guard_and_unload_cancellation(plugin):
    entered = asyncio.Event()

    async def synth(*args):
        entered.set()
        await asyncio.Event().wait()

    plugin.http.synthesize = synth
    event = Event()
    task = asyncio.create_task(plugin.send_voice_manual(event, "你好", "温柔。"))
    await entered.wait()
    assert "正在生成" in await plugin.send_voice_auto(event, "你好")
    await plugin.terminate()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not event.get_extra(TOOL_SENT) and not event.get_extra(TOOL_BUSY)
    assert not plugin.jobs and not plugin.gate.slots


def test_tool_registered_with_real_astrbot():
    from astrbot.core.provider.register import llm_tools
    from astrbot.core.star.star_handler import star_handlers_registry

    assert not any(t.name == "emotive_send_voice" for t in llm_tools.func_list)
    for method, properties in [
        ("send_voice_auto", {"text", "context_num"}),
        ("send_voice_manual", {"text", "instruction", "speech_rate"}),
    ]:
        tool = next(t for t in llm_tools.func_list if t.name == "emotive_" + method)
        assert tool.handler.__module__ == "plugin_under_test.main"
        metadata = star_handlers_registry.get_handler_by_full_name(
            "plugin_under_test.main_" + method
        )
        assert metadata.handler_module_path == "plugin_under_test.main"
        assert set(tool.parameters["properties"]) == properties


def test_root_entrypoint_initializes_and_registers(monkeypatch, tmp_path):
    from astrbot.api.star import StarTools
    from astrbot.core.star.star import star_map

    monkeypatch.setattr(StarTools, "get_data_dir", lambda *args: tmp_path)
    context = SimpleNamespace()
    instance = EmotiveTTS(context, {})
    assert instance.context is context
    assert instance.data_dir == tmp_path / "audio"
    assert star_map["plugin_under_test.main"].star_cls_type is EmotiveTTS
    assert EmotiveTTS.send_voice_auto.__module__ == "plugin_under_test.main"


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 2, 8])
async def test_tool_context_count_overrides_config_without_mutating_snapshot(plugin, count):
    import json

    plugin.config.update(context_messages=1, analysis_mode="external")
    payloads = []

    async def analyze(config, prompt, payload):
        payloads.append(json.loads(payload))
        return '{"instruction":"温柔。"}'

    plugin.http.analyze_external = analyze
    event = Event()
    req = SimpleNamespace(
        contexts=[{"role": "user", "content": str(i)} for i in range(10)],
        system_prompt="角色",
    )
    await plugin.capture(event, req)
    assert "成功" in await plugin.send_voice_auto(event, "你好", context_num=count)
    expected = [str(i) for i in range(10 - count, 10)] if count else []
    assert [m["content"] for m in payloads[0]["history"]] == expected
    assert event.get_extra(SNAPSHOT)["history"] == [{"role": "user", "content": "9"}]
    assert plugin.config["context_messages"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [-1, 51, 1.5, "5", True, None])
async def test_tool_rejects_invalid_context_count(plugin, count):
    event = Event()
    assert "context_num" in await plugin.send_voice_auto(event, "你好", context_num=count)
    assert not event.sent and not plugin.jobs


@pytest.mark.asyncio
async def test_analysis_uses_selected_platform_provider(plugin):
    from unittest.mock import AsyncMock

    plugin.config.update(auto_emotion=True, analysis_provider_id="tone-llm")
    generate = AsyncMock(return_value=SimpleNamespace(completion_text='{"instruction":"轻快。"}'))
    plugin.context = SimpleNamespace(llm_generate=generate)
    assert await plugin.instruction(Event(), "你好") == "轻快。"
    assert generate.call_args.kwargs["chat_provider_id"] == "tone-llm"
    assert generate.call_args.kwargs["tools"] is None
    assert generate.call_args.kwargs["contexts"] == []


@pytest.mark.asyncio
async def test_unselected_analysis_does_not_use_current_chat(plugin):
    from unittest.mock import AsyncMock

    plugin.config.update(auto_emotion=True, analysis_provider_id="")
    generate = AsyncMock()
    plugin.context = SimpleNamespace(llm_generate=generate)
    await plugin.instruction(Event(), "你好")
    generate.assert_not_called()


def test_separate_private_group_whitelists(plugin):
    plugin.config.update(
        private_umo_whitelist=["bot:FriendMessage:1"], group_umo_whitelist=["bot:GroupMessage:3"]
    )
    assert plugin.allowed(Event())
    assert not plugin.allowed(Event("bot:FriendMessage:2"))
    assert plugin.allowed(Event("bot:GroupMessage:3", group="3"))
    assert not plugin.allowed(Event("bot:GroupMessage:4", group="4"))


@pytest.mark.asyncio
async def test_manual_tool_tolerates_irrelevant_auto_context_argument(plugin):
    async def forbidden(*args, **kwargs):
        pytest.fail("manual tool must not analyze context")

    plugin.instruction = forbidden
    event = Event()
    result = await plugin.send_voice_manual(event, "你好呀", "温柔。", context_num=6)
    assert "成功" in result and len(event.sent) == 1


@pytest.mark.asyncio
async def test_manual_speech_rate_is_per_call_and_auto_uses_view(plugin):
    rates = []
    plugin.config["speech_rate"] = 1.5

    async def synth(config, text, instruction):
        rates.append(config["speech_rate"])
        return b"audio"

    plugin.http.synthesize = synth
    await plugin.send_voice_manual(Event(), "默认", "自然。")
    await plugin.send_voice_manual(Event(), "覆盖", "自然。", speech_rate=1.3)
    await plugin.send_voice_auto(Event(), "自动")
    assert rates == [1.12, 1.3, 1.5]
    assert plugin.config["speech_rate"] == 1.5


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [0, 2.1, True, "1.12", float("nan"), float("inf")])
async def test_invalid_manual_speech_rate_rejected(plugin, rate):
    event = Event()
    assert "speech_rate" in await plugin.send_voice_manual(
        event, "你好", "自然。", speech_rate=rate
    )
    assert not event.sent


@pytest.mark.asyncio
async def test_platform_tts_uses_plugin_transport_without_mutating_provider(plugin):
    from unittest.mock import AsyncMock

    source = {
        "type": "dashscope_tts",
        "api_key": "platform-key",
        "model": "cosyvoice-v3.5-plus",
        "dashscope_tts_voice": "voice-one",
    }
    provider = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="tts-one"), provider_config=source, get_audio=AsyncMock()
    )
    plugin.context = SimpleNamespace(get_all_tts_providers=lambda: [provider])
    plugin.config.update(
        tts_mode="平台已配置的语音模型", tts_provider_id="tts-one", speech_rate=1.4
    )
    calls = []

    async def synth(config, text, instruction):
        calls.append((config, text, instruction))
        return b"audio"

    plugin.http.synthesize = synth
    await plugin.send_voice_manual(Event(), "你好", "温柔。", speech_rate=1.25)
    config, text, instruction = calls[0]
    assert config["api_key"] == "platform-key" and config["voice"] == "voice-one"
    assert config["speech_rate"] == 1.25 and instruction == "温柔。"
    provider.get_audio.assert_not_called()
    assert "speech_rate" not in source and plugin.config["speech_rate"] == 1.4
    plugin.instruction = AsyncMock(return_value="轻快。")
    await plugin.send_voice_auto(Event(), "自动")
    assert calls[-1][0]["speech_rate"] == 1.4
    assert calls[-1][2] == "轻快。"
    plugin.instruction.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["external", "platform"])
@pytest.mark.parametrize("valid", [True, False])
async def test_voice_persona_reaches_director_without_raw_tts_append(plugin, mode, valid):
    import json
    from unittest.mock import AsyncMock

    profile = "清亮女孩，俏皮但安慰时认真。"
    plugin.config.update(
        auto_emotion=True,
        analysis_mode=mode,
        analysis_provider_id="tone-llm",
        voice_persona=profile,
        fallback_instruction="自然说话。",
    )
    event = Event()
    event.set_extra(
        SNAPSHOT,
        {
            "history": [{"role": "user", "content": "我今天很难过"}],
            "user": "陪我一会儿",
            "persona": "朋友关系",
        },
    )
    instruction = "像在身边轻声陪伴。" if valid else "啊" * 51
    raw = json.dumps({"intent": "安慰", "evidence": "很难过", "instruction": instruction})
    if mode == "external":
        call = AsyncMock(return_value=raw)
        plugin.http.analyze_external = call
    else:
        call = AsyncMock(return_value=SimpleNamespace(completion_text=raw))
        plugin.context = SimpleNamespace(llm_generate=call)
    result = await plugin.instruction(event, "我在这儿。")
    assert result == (instruction if valid else "自然说话。")
    payload = json.loads(
        call.call_args.args[2] if mode == "external" else call.call_args.kwargs["prompt"]
    )
    assert payload["voice_persona"] == profile
    assert payload["persona"] == "朋友关系"
    assert payload["history"][0]["content"] == "我今天很难过"
    assert payload["reply"] == "我在这儿。"
    assert profile not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("valid", [True, False])
async def test_temporary_analysis_debug_toggle_and_fallback(plugin, monkeypatch, enabled, valid):
    from unittest.mock import AsyncMock, Mock

    from plugin_under_test.src import director

    messages = []
    info = Mock(side_effect=lambda template, *args: messages.append(template % args))
    monkeypatch.setattr(director.logger, "info", info)
    plugin.config.update(
        auto_emotion=True,
        analysis_mode="external",
        analysis_debug=enabled,
        api_key="private-auth-key",
        analysis_api_key="private-analysis-key",
        analysis_headers='{"Authorization":"private-header"}',
    )
    plugin.http.analyze_external = AsyncMock(
        return_value='{"instruction":"温柔聊天。"}' if valid else "bad json"
    )
    event = Event()
    event.set_extra(SNAPSHOT, {"user": "测试上下文"})
    result = await plugin.instruction(event, "我在这里。")
    if enabled:
        logs = "\n".join(messages)
        assert director.DIRECTOR_PROMPT in logs
        assert "测试上下文" in logs and "我在这里。" in logs and result in logs
        assert not any(
            secret in logs
            for secret in ("private-auth-key", "private-analysis-key", "private-header")
        )
    else:
        info.assert_not_called()

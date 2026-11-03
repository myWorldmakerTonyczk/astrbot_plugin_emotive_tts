import asyncio
import json
from types import SimpleNamespace

import pytest
from astrbot.api.star import StarTools
from astrbot.core.utils.session_lock import session_lock_manager
from plugin_under_test.main import EmotiveTTS
from plugin_under_test.src.events import SNAPSHOT, TOOL_SENT
from plugin_under_test.src.history import insert_voice_at_tool_result, queue_voice_history
from test_plugin import Event


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda *args: tmp_path)
    instance = EmotiveTTS(SimpleNamespace(), {"auto_emotion": False})

    async def synth(*args):
        return b"audio"

    async def close():
        pass

    instance.http = SimpleNamespace(synthesize=synth, close=close)
    return instance


class Manager:
    def __init__(self):
        self.current = "original"
        self.records = {"original": [{"role": "user", "content": "你好"}]}
        self.updates = []

    async def get_curr_conversation_id(self, umo):
        return self.current

    async def get_conversation(self, umo, cid):
        if cid not in self.records:
            return None
        return SimpleNamespace(history=json.dumps(self.records[cid]))

    async def update_conversation(self, umo, cid, history):
        self.records[cid] = history
        self.updates.append((umo, cid))


@pytest.mark.asyncio
async def test_success_waits_for_framework_save_and_keeps_original_conversation(plugin):
    manager = Manager()
    plugin.context.conversation_manager = manager
    event = Event()
    event.set_extra(SNAPSHOT, {"conversation_id": "original"})
    async with session_lock_manager.acquire_lock(event.unified_msg_origin):
        result = await asyncio.wait_for(
            plugin.send_voice_manual(event, "你好呀", "轻快。"), timeout=1
        )
        assert "成功" in result and "后续文字回复之前" in result
        await asyncio.sleep(0)
        assert not manager.updates
        manager.records["original"].extend(
            [
                {"role": "assistant", "content": None, "tool_calls": [{"id": "voice"}]},
                {"role": "tool", "tool_call_id": "voice", "content": result},
                {"role": "assistant", "content": "普通回复"},
            ]
        )
        manager.current = "another"
    await asyncio.gather(*plugin.jobs)
    assert manager.records["original"][-2] == {"role": "assistant", "content": "你好呀"}
    assert manager.records["original"][-1]["content"] == "普通回复"
    assert manager.updates == [(event.unified_msg_origin, "original")]
    await plugin.send_voice_auto(event, "不应重复")
    assert len(manager.updates) == 1


@pytest.mark.asyncio
async def test_failed_delivery_never_saves_speech(plugin):
    manager = Manager()
    plugin.context.conversation_manager = manager
    event = Event(fail=True)
    assert "失败" in await plugin.send_voice_manual(event, "没送达", "轻快。")
    assert not manager.updates and not plugin.jobs
    assert await plugin.voice_memory.recall(event.unified_msg_origin, "original") == []
    assert not event.get_extra(TOOL_SENT)


@pytest.mark.asyncio
async def test_identical_final_reply_is_not_added_twice(plugin):
    manager = Manager()
    manager.records["original"].extend(
        [
            {"role": "tool", "content": "[test-anchor]"},
            {"role": "assistant", "content": "你好呀"},
        ]
    )
    plugin.context.conversation_manager = manager
    assert await queue_voice_history(plugin, Event(), "你好呀", "[test-anchor]")
    await asyncio.gather(*plugin.jobs)
    assert len(manager.records["original"]) == 3 and not manager.updates


@pytest.mark.asyncio
async def test_missing_or_deleted_conversation_is_not_recreated(plugin):
    manager = Manager()
    plugin.context.conversation_manager = manager
    manager.current = None
    assert not await queue_voice_history(plugin, Event(), "你好呀", "[test-anchor]")
    manager.current = "deleted"
    assert await queue_voice_history(plugin, Event(), "你好呀", "[test-anchor]")
    await asyncio.gather(*plugin.jobs)
    assert not manager.updates


@pytest.mark.asyncio
async def test_storage_failure_does_not_claim_delivery_failed(plugin):
    manager = Manager()
    plugin.context.conversation_manager = manager

    async def fail(*args, **kwargs):
        raise RuntimeError("private storage error")

    manager.update_conversation = fail
    event = Event()
    async with session_lock_manager.acquire_lock(event.unified_msg_origin):
        result = await plugin.send_voice_manual(event, "你好呀", "轻快。")
        assert "成功" in result
        manager.records["original"].append({"role": "tool", "content": result})
    await asyncio.gather(*plugin.jobs)
    assert event.get_extra(TOOL_SENT) and len(event.sent) == 1
    assert "已发送" in await plugin.send_voice_auto(event, "别重发")


def test_insertion_preserves_parallel_tool_results_and_later_turns():
    history = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "sent [unique]"},
        {"role": "tool", "tool_call_id": "b", "content": "weather"},
        {"role": "assistant", "content": "后续正文"},
        {"role": "user", "content": "下一轮"},
    ]
    assert insert_voice_at_tool_result(history, "语音内容", "[unique]")
    assert [m["role"] for m in history] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
        "assistant",
        "user",
    ]
    assert history[3]["content"] == "语音内容" and history[4]["content"] == "后续正文"
    assert not insert_voice_at_tool_result(history, "语音内容", "[unique]")


def test_missing_anchor_never_appends_to_an_old_turn():
    history = [{"role": "assistant", "content": "其他回复"}]
    assert not insert_voice_at_tool_result(history, "语音内容", "[missing]")
    assert len(history) == 1

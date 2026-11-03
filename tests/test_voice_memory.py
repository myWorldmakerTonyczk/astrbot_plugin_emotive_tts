from types import SimpleNamespace

import pytest
from plugin_under_test.src.events import SNAPSHOT
from plugin_under_test.src.voice_memory import VoiceMemory, remember_success, supplement_request
from test_plugin import Event


@pytest.mark.asyncio
async def test_durable_memory_is_scoped_and_bounded(tmp_path):
    path = tmp_path / "memory.sqlite3"
    memory = VoiceMemory(path)
    for i in range(55):
        await memory.remember("u", "c", str(i), str(i))
    assert len(await VoiceMemory(path).recall("u", "c")) == 10
    assert (await memory.recall("u", "c"))[-1]["text"] == "54"
    assert await memory.recall("other", "c") == []
    assert await memory.recall("u", "new") == []
    with memory.connection() as db:
        assert db.execute("SELECT count(*) FROM speech").fetchone()[0] == 50
        db.execute("UPDATE speech SET created=0")
    assert await memory.recall("u", "c") == []


@pytest.mark.asyncio
async def test_next_regular_request_gets_missing_speech_without_tool_call(tmp_path):
    memory = VoiceMemory(tmp_path / "memory.sqlite3")
    event = Event()
    await memory.remember(event.unified_msg_origin, "cid", "我会等你回来。", "one")
    plugin = SimpleNamespace(voice_memory=memory)
    req = SimpleNamespace(
        conversation=SimpleNamespace(cid="cid"), contexts=[], system_prompt="角色设定"
    )
    records = await supplement_request(plugin, event, req)
    assert records[0]["text"] == "我会等你回来。"
    assert "我会等你回来。" in req.system_prompt and req.system_prompt.startswith("角色设定")
    assert req.contexts == []
    await supplement_request(plugin, event, req)
    assert req.system_prompt.count("<emotive_voice_memory>") == 1
    req.conversation.cid = "new"
    await supplement_request(plugin, event, req)
    assert "我会等你回来。" not in req.system_prompt


@pytest.mark.asyncio
async def test_existing_plain_and_tool_history_deduplicate(tmp_path):
    memory = VoiceMemory(tmp_path / "memory.sqlite3")
    event = Event()
    await memory.remember(event.unified_msg_origin, "cid", "已发送的话", "one")
    plugin = SimpleNamespace(voice_memory=memory)
    req = SimpleNamespace(
        conversation=SimpleNamespace(cid="cid"),
        contexts=[{"role": "assistant", "content": "已发送的话"}],
        system_prompt="角色",
    )
    assert await supplement_request(plugin, event, req) == []
    assert req.system_prompt == "角色"
    req.contexts = [{"role": "tool", "content": "语音已成功发送。已说出的正文：已发送的话"}]
    assert await supplement_request(plugin, event, req)
    assert (
        req.system_prompt == "角色"
    )  # Analysis still receives speech, chat already sees tool result.


@pytest.mark.asyncio
async def test_success_stored_before_framework_history_save(tmp_path):
    memory = VoiceMemory(tmp_path / "memory.sqlite3")
    plugin = SimpleNamespace(voice_memory=memory)
    event = Event()
    event.set_extra(SNAPSHOT, {"conversation_id": "original"})
    assert await remember_success(plugin, event, "成功语音", "marker")
    assert (await VoiceMemory(memory.path).recall(event.unified_msg_origin, "original"))[0][
        "text"
    ] == "成功语音"

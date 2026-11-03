import asyncio
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin_under_test.src.network import HttpServices, ServiceError
from plugin_under_test.src.queue_manager import WorkGate
from plugin_under_test.src.seed_lab import PRESETS, SeedLab
from plugin_under_test.src.view import seed_preview as api
from plugin_under_test.src.view.settings import SCHEMA, SettingsView, revision, validate_changes


class Config(dict):
    def save_config(self):
        self.saved = dict(self)


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 240)
    return out.getvalue()


@pytest.fixture
def preview(tmp_path):
    defaults = {k: s["default"] for k, s in SCHEMA.items()}
    config = Config(
        {
            **defaults,
            "voice": "test-voice",
            "api_key": "private-key",
            "analysis_provider_id": "director",
            "analysis_debug": False,
            "tts_seed": 37,
        }
    )
    plugin = SimpleNamespace(
        config=config,
        context=SimpleNamespace(
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(completion_text='{"instruction":"轻巧自然聊天。"}')
            )
        ),
        http=SimpleNamespace(synthesize=AsyncMock(return_value=wav_bytes())),
        data_dir=tmp_path / "audio",
        jobs=set(),
        gate=WorkGate(),
        closing=False,
    )
    return api.SeedPreviewView(SettingsView(plugin), SCHEMA, revision)


def body(monkeypatch, data):
    monkeypatch.setattr(api, "request", SimpleNamespace(json=AsyncMock(return_value=data)))


def unpack(response):
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_pipeline_fixes_each_instruction_across_seeds_and_persists(preview):
    p = preview.plugin
    before = dict(p.config)
    batch = preview.lab.start(p.config, 3, revision(p.config))
    assert batch["seeds"][0] == 37 and len(set(batch["seeds"])) == 3
    p.config["speech_rate"] = 1.7  # Generation uses its own immutable saved snapshot.
    await preview.lab.task
    record = preview.lab.get(batch["id"])
    assert record["status"] == "complete"
    assert p.context.llm_generate.await_count == 5
    assert p.http.synthesize.await_count == 15
    for scene in PRESETS:
        calls = [c for c in p.http.synthesize.call_args_list if c.args[1] == scene["reply"]]
        assert len(calls) == 3
        assert {c.args[0]["tts_seed"] for c in calls} == set(batch["seeds"])
        assert {c.args[2] for c in calls} == {record["instructions"][scene["id"]]["instruction"]}
        assert all(c.args[0]["speech_rate"] == before["speech_rate"] for c in calls)
    assert p.config["tts_seed"] == 37 and not hasattr(p.config, "saved")
    disk = (preview.lab.root / (batch["id"] + ".json")).read_text(encoding="utf-8")
    assert "private-key" not in disk and "api_key" not in disk
    restored = SeedLab(p, preview.lab.defaults)
    assert len(restored.listing()["batches"]) == 1
    assert restored.audio(batch["id"], record["samples"][0]["id"]) == wav_bytes()


@pytest.mark.asyncio
async def test_apply_updates_only_seed_and_retains_comparisons(preview, monkeypatch):
    p = preview.plugin
    batch = preview.lab.start(p.config, 2, revision(p.config))
    await preview.lab.task
    selected = batch["seeds"][1]
    before = dict(p.config)
    body(monkeypatch, {"batch_id": batch["id"], "seed": selected, "revision": revision(p.config)})
    assert (await preview.apply()).status_code == 200
    assert dict(p.config) == {**before, "tts_seed": selected}
    assert p.config.saved["tts_seed"] == selected
    assert len(preview.lab.listing()["batches"]) == 1
    assert len(list((preview.lab.root / batch["id"]).glob("*.wav"))) == 10
    body(monkeypatch, {"batch_id": batch["id"], "seed": selected, "revision": "stale"})
    assert (await preview.apply()).status_code == 409


@pytest.mark.asyncio
async def test_failed_sample_cannot_be_applied_but_other_seed_completes(preview, monkeypatch):
    p = preview.plugin

    async def synth(config, text, instruction):
        if config["tts_seed"] == 37:
            raise ServiceError("API HTTP 400")
        return wav_bytes()

    p.http.synthesize.side_effect = synth
    batch = preview.lab.start(p.config, 2, revision(p.config))
    await preview.lab.task
    assert preview.lab.get(batch["id"])["status"] == "partial"
    body(monkeypatch, {"batch_id": batch["id"], "seed": 37, "revision": revision(p.config)})
    assert (await preview.apply()).status_code == 400
    body(
        monkeypatch,
        {"batch_id": batch["id"], "seed": batch["seeds"][1], "revision": revision(p.config)},
    )
    assert (await preview.apply()).status_code == 200


@pytest.mark.asyncio
async def test_cancel_keeps_completed_audio_and_cannot_escape_storage(preview):
    p = preview.plugin
    started = asyncio.Event()

    async def synth(config, text, instruction):
        if text != PRESETS[0]["reply"]:
            started.set()
            await asyncio.Event().wait()
        return wav_bytes()

    p.http.synthesize.side_effect = synth
    batch = preview.lab.start(p.config, 2, revision(p.config))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(ValueError, match="已有"):
        preview.lab.start(p.config, 2, revision(p.config))
    await preview.lab.cancel(batch["id"])
    result = preview.lab.get(batch["id"])
    assert result["status"] == "cancelled"
    assert sum(s["status"] == "ok" for s in result["samples"]) == 2
    for batch_id, sample_id in (("../../bad", "bad"), (batch["id"], "../../bad")):
        with pytest.raises(ValueError):
            preview.lab.audio(batch_id, sample_id)
    await preview.lab.delete(batch["id"])
    assert not preview.lab.listing()["batches"]


@pytest.mark.asyncio
async def test_fallback_and_disabled_analysis_are_explicit(preview):
    p = preview.plugin
    p.context.llm_generate.return_value.completion_text = "invalid-json"
    first = preview.lab.start(p.config, 2, revision(p.config))
    await preview.lab.task
    assert all(
        x["source"] == "fallback" for x in preview.lab.get(first["id"])["instructions"].values()
    )
    p.config["auto_emotion"] = False
    second = preview.lab.start(p.config, 2, revision(p.config))
    await preview.lab.task
    assert p.context.llm_generate.await_count == 5
    assert all(
        x["source"] == "default" for x in preview.lab.get(second["id"])["instructions"].values()
    )


@pytest.mark.asyncio
async def test_seed_api_invalid_requests_and_save_failure(preview, monkeypatch):
    for payload in (None, {"count": 3, "revision": "stale"}):
        body(monkeypatch, payload)
        assert (await preview.start()).status_code in (400, 409)
    for count in (True, 1, 5, "3"):
        body(monkeypatch, {"count": count, "revision": revision(preview.plugin.config)})
        assert (await preview.start()).status_code == 400
    batch = preview.lab.start(preview.plugin.config, 2, revision(preview.plugin.config))
    await preview.lab.task
    before = dict(preview.plugin.config)

    def fail():
        raise OSError("private failure")

    preview.plugin.config.save_config = fail
    body(
        monkeypatch,
        {
            "batch_id": batch["id"],
            "seed": batch["seeds"][1],
            "revision": revision(preview.plugin.config),
        },
    )
    response = await preview.apply()
    assert response.status_code == 500 and dict(preview.plugin.config) == before
    assert "private failure" not in response.body.decode()


@pytest.mark.asyncio
async def test_transport_seed_range_and_omission(monkeypatch):
    http = HttpServices()
    post = AsyncMock(return_value={"output": {"audio": {"url": "https://audio.example.com/a.wav"}}})
    monkeypatch.setattr(http, "post_json", post)
    monkeypatch.setattr(http, "download_audio", AsyncMock(return_value=wav_bytes()))
    for seed in (-1, 0, 37, 65535):
        await http.synthesize({"voice": "voice", "tts_seed": seed}, "你好", "自然聊天。")
        data = post.call_args.args[1]["input"]
        assert ("seed" not in data) if seed == -1 else data["seed"] == seed
    for seed in (True, -2, 65536, "37", 1.5):
        with pytest.raises(ServiceError):
            await http.synthesize({"voice": "voice", "tts_seed": seed}, "你好", "自然聊天。")
        with pytest.raises(ValueError):
            validate_changes({"tts_seed": seed})


@pytest.mark.asyncio
async def test_immediate_cancel_and_restarted_incomplete_records(preview):
    batch = preview.lab.start(preview.plugin.config, 2, revision(preview.plugin.config))
    await preview.lab.cancel(batch["id"])
    assert preview.lab.get(batch["id"])["status"] == "cancelled"
    record = preview.lab.get(batch["id"])
    record["status"] = "running"
    record["samples"][0]["status"] = "running"
    preview.lab._store(record)
    loaded = SeedLab(preview.plugin, preview.lab.defaults)
    assert loaded.get(batch["id"])["status"] == "interrupted"
    assert loaded.get(batch["id"])["samples"][0]["status"] == "interrupted"
    assert loaded.task is None


@pytest.mark.asyncio
async def test_platform_preview_reuses_connection_but_preserves_current_controls(
    preview, monkeypatch
):
    p = preview.plugin
    p.config.update(tts_mode="平台已配置的语音模型", tts_provider_id="ali-voice", speech_rate=1.2)
    provider = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="ali-voice"),
        provider_config={
            "type": "dashscope_tts",
            "api_key": "platform-key",
            "model": "cosyvoice-v3.5-plus",
            "dashscope_tts_voice": "platform-voice",
        },
    )
    p.context.get_all_tts_providers = lambda: [provider]
    batch = preview.lab.start(p.config, 2, revision(p.config))
    await preview.lab.task
    record = preview.lab.get(batch["id"])
    assert record["settings"]["tts_mode"] == "平台已配置的语音模型"
    assert record["settings"]["voice"] == "platform-voice"
    for call in p.http.synthesize.call_args_list:
        config = call.args[0]
        assert config["api_key"] == "platform-key"
        assert config["voice"] == "platform-voice" and config["speech_rate"] == 1.2
        assert config["tts_seed"] in batch["seeds"]
    listing = json.dumps(preview.lab.listing(), ensure_ascii=False)
    assert "platform-key" not in listing and "private-key" not in listing
    sample = record["samples"][0]
    body(monkeypatch, {"batch_id": batch["id"], "sample_id": sample["id"]})
    response = await preview.audio()
    assert response.status_code == 200 and unpack(response)["audio_base64"]
    body(monkeypatch, {"batch_id": "../bad", "sample_id": "../bad"})
    assert (await preview.audio()).status_code == 404


@pytest.mark.asyncio
async def test_manual_seeds_are_exact_and_allow_one_candidate(preview, monkeypatch):
    body(monkeypatch, {"seeds": [0], "revision": revision(preview.plugin.config)})
    response = await preview.start()
    assert response.status_code == 200
    await preview.lab.task
    batch = preview.lab.listing()["batches"][0]
    assert batch["seeds"] == [0]
    assert len(batch["samples"]) == 5
    assert all(c.args[0]["tts_seed"] == 0 for c in preview.plugin.http.synthesize.call_args_list)
    for seeds in ([], [1, 1], [True], [-1], [65536], ["37"], [1, 2, 3, 4, 5], "37", [{}]):
        body(monkeypatch, {"seeds": seeds, "revision": revision(preview.plugin.config)})
        assert (await preview.start()).status_code == 400

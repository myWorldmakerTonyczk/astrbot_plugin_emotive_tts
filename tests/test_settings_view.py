import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin_under_test.src.queue_manager import WorkGate
from plugin_under_test.src.view import settings as module


class Config(dict):
    def save_config(self):
        self.saved = dict(self)


@pytest.fixture
def view():
    config = Config({k: v["default"] for k, v in module.SCHEMA.items()})
    config.update(api_key="private-key", tts_headers='{"Authorization":"secret"}')
    return module.SettingsView(SimpleNamespace(config=config, closing=False, gate=WorkGate()))


def test_all_settings_hidden_in_legacy_form_and_secrets_masked(view):
    schema = json.loads(
        (module.SCHEMA_PATH.parents[2] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    assert [k for k, v in schema.items() if not v.get("invisible")] == []
    result = module.settings_payload(view.plugin.config)
    assert result["values"]["api_key"] == "" and result["configured"]["api_key"]
    assert "private-key" not in json.dumps(result)
    assert "Authorization" not in result["values"]["tts_headers"]


@pytest.mark.asyncio
async def test_save_keeps_masked_secrets_and_persists(view, monkeypatch):
    body = {
        "revision": module.revision(view.plugin.config),
        "changes": {"speech_rate": 1.2, "api_key": ""},
    }
    monkeypatch.setattr(module, "request", SimpleNamespace(json=AsyncMock(return_value=body)))
    response = await view.save()
    assert response.status_code == 200
    assert view.plugin.config.saved["api_key"] == "private-key"
    assert view.plugin.config.saved["speech_rate"] == 1.2


@pytest.mark.asyncio
async def test_conflict_and_busy_queue(view, monkeypatch):
    body = {"revision": "stale", "changes": {"speech_rate": 1.3}}
    monkeypatch.setattr(module, "request", SimpleNamespace(json=AsyncMock(return_value=body)))
    assert (await view.save()).status_code == 409
    body.update(revision=module.revision(view.plugin.config), changes={"concurrency": 3})
    view.plugin.gate.pending = 1
    assert (await view.save()).status_code == 409


@pytest.mark.asyncio
async def test_clear_secret_and_apply_queue(view, monkeypatch):
    body = {
        "revision": module.revision(view.plugin.config),
        "changes": {"queue_capacity": 10},
        "clear_secrets": ["api_key"],
    }
    monkeypatch.setattr(module, "request", SimpleNamespace(json=AsyncMock(return_value=body)))
    assert (await view.save()).status_code == 200
    assert view.plugin.config["api_key"] == "" and view.plugin.gate.capacity == 10


@pytest.mark.parametrize(
    "change",
    [
        {"speech_rate": 99},
        {"context_messages": True},
        {"tts_mode": "invalid"},
        {"tts_headers": "bad"},
        {"tts_endpoint": "http://localhost"},
        {"fallback_instruction": ""},
    ],
)
def test_invalid_changes_rejected(change):
    with pytest.raises(ValueError):
        module.validate_changes(change)


@pytest.mark.asyncio
async def test_save_failure_restores_running_settings(view, monkeypatch):
    before = dict(view.plugin.config)

    def fail():
        raise OSError("private")

    view.plugin.config.save_config = fail
    monkeypatch.setattr(
        module,
        "request",
        SimpleNamespace(
            json=AsyncMock(
                return_value={"revision": module.revision(before), "changes": {"speech_rate": 1.4}}
            )
        ),
    )
    assert (await view.save()).status_code == 500
    assert dict(view.plugin.config) == before


@pytest.mark.asyncio
async def test_api_mode_can_be_saved_in_view(view, monkeypatch):
    body = {
        "revision": module.revision(view.plugin.config),
        "changes": {"tts_mode": "自定义兼容接口"},
    }
    monkeypatch.setattr(module, "request", SimpleNamespace(json=AsyncMock(return_value=body)))
    assert (await view.save()).status_code == 200
    assert view.plugin.config.saved["tts_mode"] == "自定义兼容接口"
    assert view.plugin.config.saved["api_key"] == "private-key"


def test_provider_dropdown_uses_platform_chat_models_without_credentials(view):
    view.plugin.context = SimpleNamespace(
        get_all_providers=lambda: [
            SimpleNamespace(meta=lambda: SimpleNamespace(id="tone-llm", model="qwen-plus"))
        ]
    )
    payload = view.payload()
    field = payload["schema"]["analysis_provider_id"]
    assert field["options"] == ["", "tone-llm"]
    assert "qwen-plus" in field["labels"][1]
    assert "private-key" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_voice_persona_save_reload_and_clear(view, monkeypatch):
    profile = "清亮女孩，自然聊天。"
    for value in (profile, ""):
        body = {
            "revision": module.revision(view.plugin.config),
            "changes": {"voice_persona": value},
        }
        monkeypatch.setattr(module, "request", SimpleNamespace(json=AsyncMock(return_value=body)))
        assert (await view.save()).status_code == 200
        assert view.plugin.config.saved["voice_persona"] == value
        assert view.payload()["values"]["voice_persona"] == value
    with pytest.raises(ValueError, match="2000"):
        module.validate_changes({"voice_persona": "啊" * 2001})

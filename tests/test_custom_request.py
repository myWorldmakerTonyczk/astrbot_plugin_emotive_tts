import base64
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin_under_test.src.custom_request import (
    DEFAULT_BODY,
    audio_extension,
    decode_audio,
    extract_path,
    render_template,
)
from plugin_under_test.src.network import HttpServices, ServiceError
from plugin_under_test.src.seed_lab import playback_wav
from plugin_under_test.src.view.settings import settings_payload, validate_changes


def wav():
    f = io.BytesIO()
    with wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 240)
    return f.getvalue()


def test_typed_template_and_nonrecursive_replacement():
    values = {
        "text": '"你好"\n<api_key>',
        "instruction": "温柔",
        "model": "m",
        "voice": "v",
        "speech_rate": 1.12,
        "seed": 37,
        "api_key": "secret",
    }
    result = render_template(DEFAULT_BODY, values)
    assert result["input"]["text"] == values["text"]
    assert result["input"]["rate"] == 1.12 and type(result["input"]["seed"]) is int
    assert render_template('{"Authorization":"Bearer <api_key>"}', values, headers=True) == {
        "Authorization": "Bearer secret"
    }
    assert json.loads(json.dumps(result)) == result
    values["seed"] = -1
    assert "seed" not in render_template(DEFAULT_BODY, values)["input"]
    with pytest.raises(ValueError):
        render_template('{"a":"<unknown>"}', values)


@pytest.mark.parametrize("mode", ["url", "binary", "base64", "hex"])
@pytest.mark.asyncio
async def test_four_audio_modes_and_custom_transport(mode):
    data = wav()
    http = HttpServices()
    raw = {
        "url": json.dumps({"nested": [{"sound": "https://audio.example.com/file.wav"}]}).encode(),
        "binary": data,
        "base64": json.dumps({"nested": [{"sound": base64.b64encode(data).decode()}]}).encode(),
        "hex": json.dumps({"nested": [{"sound": data.hex()}]}).encode(),
    }[mode]
    http.post_audio_request = AsyncMock(return_value=raw)
    http.download_audio = AsyncMock(return_value=data)
    config = {
        "tts_mode": "自定义兼容接口",
        "tts_endpoint": "https://tts.example.com/speech",
        "tts_response_mode": mode,
        "tts_audio_path": "nested[0].sound",
        "tts_body_template": '{"say":"<text>","speed":"<speech_rate>","seed":"<seed>"}',
        "tts_headers": '{"X-Key":"<api_key>"}',
        "api_key": "secret",
        "tts_seed": 37,
    }
    assert await http.synthesize(config, "你好", "温柔") == data
    args = http.post_audio_request.call_args.args
    assert args[1] == {"say": "你好", "speed": 1.12, "seed": 37}
    assert args[2]["X-Key"] == "secret"
    assert http.download_audio.await_count == (1 if mode == "url" else 0)


def test_paths_encodings_and_format_preservation():
    assert extract_path({"a.b": {"x/y": [3]}}, "/a.b/x~1y/0") == 3
    with pytest.raises(ValueError):
        extract_path({"data": None}, "data.url")
    for data, extension in [
        (wav(), "wav"),
        (b"ID3" + b"\0" * 40, "mp3"),
        (b"OggS" + b"\0" * 40, "ogg"),
        (b"fLaC" + b"\0" * 40, "flac"),
    ]:
        assert audio_extension(data) == extension
        assert decode_audio(base64.b64encode(data).decode(), "base64") == data
        assert decode_audio(data.hex(), "hex") == data
        assert playback_wav(data) == data
    for value in ["not-audio", "bad*base64"]:
        with pytest.raises(ValueError):
            decode_audio(value, "base64")
    with pytest.raises(ValueError):
        audio_extension(b'{"error":"failed"}')


@pytest.mark.asyncio
async def test_bad_response_mapping_and_header_injection_fail_closed():
    http = HttpServices()
    http.post_audio_request = AsyncMock(return_value=b'{"data":null}')
    c = {
        "tts_mode": "自定义兼容接口",
        "tts_body_template": "{}",
        "tts_response_mode": "base64",
        "tts_audio_path": "data.audio",
    }
    with pytest.raises(ServiceError, match="invalid audio response mapping"):
        await http.synthesize(c, "你好", "自然")
    c["tts_headers"] = '{"X-Text":"<text>"}'
    http.post_audio_request.reset_mock()
    with pytest.raises(ServiceError):
        await http.synthesize(c, "a\r\nSecret: x", "自然")
    http.post_audio_request.assert_not_awaited()


def test_body_templates_are_validated_and_secrets_not_returned():
    body = '{"token":"private-token","text":"<text>"}'
    assert validate_changes({"tts_body_template": body})["tts_body_template"] == body
    assert settings_payload({"tts_body_template": body})["values"]["tts_body_template"] == ""
    assert settings_payload({})["values"]["tts_body_template"] == DEFAULT_BODY
    for bad in ["[]", '{"x":"<missing>"}', '{"__proto__":{}}']:
        with pytest.raises(ValueError):
            validate_changes({"tts_body_template": bad})


@pytest.mark.asyncio
async def test_single_sentence_test_uses_saved_config_and_does_not_persist(tmp_path, monkeypatch):
    from plugin_under_test.src.queue_manager import WorkGate
    from plugin_under_test.src.view import request_test as module
    from plugin_under_test.src.view.settings import SCHEMA, SettingsView, revision

    config = {k: v["default"] for k, v in SCHEMA.items()}
    config.update(tts_mode="自定义兼容接口", auto_emotion=False, analysis_debug=False)
    http = SimpleNamespace(synthesize=AsyncMock(return_value=wav()))
    plugin = SimpleNamespace(
        config=config,
        http=http,
        context=SimpleNamespace(),
        jobs=set(),
        gate=WorkGate(),
        closing=False,
    )
    settings = SettingsView(plugin)
    monkeypatch.setattr(
        module,
        "request",
        SimpleNamespace(json=AsyncMock(return_value={"revision": revision(config)})),
    )
    response = await module.test_request(settings, SCHEMA, revision)
    assert response.status_code == 200
    assert json.loads(response.body)["format"] == "wav"
    assert http.synthesize.await_count == 1
    assert not plugin.jobs and not settings.test_running
    assert list(tmp_path.iterdir()) == []

"""Declarative JSON request templates and common synchronous audio responses."""

import asyncio
import base64
import binascii
import json
import re

DEFAULT_BODY = json.dumps(
    {
        "model": "<model>",
        "input": {
            "text": "<text>",
            "instruction": "<instruction>",
            "voice": "<voice>",
            "rate": "<speech_rate>",
            "seed": "<seed>",
            "format": "wav",
            "sample_rate": 24000,
        },
    },
    ensure_ascii=False,
    indent=2,
)
VARIABLES = {"text", "instruction", "model", "voice", "speech_rate", "seed", "api_key"}
MARKER = re.compile(r"<([a-zA-Z_][a-zA-Z_0-9]*)>")
OMIT = object()


def parse_template(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("模板必须是JSON对象。")

    def check(obj, depth=0):
        if depth > 20:
            raise ValueError("模板嵌套层数过多。")
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("__proto__", "constructor", "prototype"):
                    raise ValueError("模板字段名称不可使用保留字。")
                check(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                check(v, depth + 1)
        elif isinstance(obj, str):
            if any(name not in VARIABLES for name in MARKER.findall(obj)):
                raise ValueError("模板包含未知变量。")

    check(value)
    return value


def render_template(raw, variables, headers=False):
    def render(obj):
        if isinstance(obj, str):
            match = MARKER.fullmatch(obj)
            if match:
                name = match[1]
                if name == "seed" and variables[name] == -1 and not headers:
                    return OMIT
                return str(variables[name]) if headers else variables[name]
            return MARKER.sub(lambda m: str(variables[m[1]]), obj)
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                value = render(value)
                if value is not OMIT:
                    result[key] = value
            return result
        if isinstance(obj, list):
            result = [render(v) for v in obj]
            if any(v is OMIT for v in result):
                raise ValueError("数组中不能使用未设置的seed变量。")
            return result
        return obj

    return render(parse_template(raw))


def extract_path(value, path):
    if not isinstance(path, str) or not path or len(path) > 500:
        raise ValueError("请填写音频字段路径。")
    # data.audio or outputs[0].audio; literal dots in keys use JSON Pointer /a.b.
    parts = (
        path[1:].split("/")
        if path.startswith("/")
        else path.replace("[", ".").replace("]", "").split(".")
    )
    try:
        for part in parts:
            part = part.replace("~1", "/").replace("~0", "~") if path.startswith("/") else part
            value = value[int(part)] if isinstance(value, list) and part.isdigit() else value[part]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("响应中找不到配置的音频字段。") from None
    return value


def audio_extension(data):
    if len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 10 and (data[:3] == b"ID3" or (data[0] == 255 and data[1] & 224 == 224)):
        return "mp3"
    if data.startswith(b"OggS") and len(data) >= 27:
        return "ogg"
    if data.startswith(b"fLaC") and len(data) >= 42:
        return "flac"
    raise ValueError("未识别到WAV、MP3、OGG或FLAC音频；不支持裸PCM。")


def decode_audio(value, kind):
    if not isinstance(value, str) or not value:
        raise ValueError("音频字段不是有效文本。")
    try:
        if kind == "base64":
            if value.startswith("data:"):
                value = value.split(",", 1)[1]
            data = base64.b64decode("".join(value.split()), validate=True)
        else:
            data = bytes.fromhex(value)
    except (ValueError, binascii.Error, IndexError):
        raise ValueError("音频编码与选择的解码方式不匹配。") from None
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("音频文件超过32MB限制。")
    audio_extension(data)
    return data


async def synthesize_custom(http, config, text, instruction):
    # Local import avoids a transport/template dependency cycle.
    from .network import ServiceError, headers_from_config

    variables = {
        "text": text,
        "instruction": instruction,
        "model": config.get("tts_model", ""),
        "voice": config.get("voice", ""),
        "speech_rate": float(config.get("speech_rate", 1.12)),
        "seed": config.get("tts_seed", -1),
        "api_key": config.get("api_key", ""),
    }
    try:
        headers = headers_from_config(
            render_template(config.get("tts_headers") or "{}", variables, headers=True),
            variables["api_key"] if config.get("tts_auto_auth", True) else "",
        )
        body = render_template(config.get("tts_body_template") or DEFAULT_BODY, variables)
    except (ValueError, TypeError, KeyError):
        raise ServiceError("invalid request template") from None
    timeout = float(config.get("tts_timeout", 30))
    async with asyncio.timeout(timeout):
        raw = await http.post_audio_request(config.get("tts_endpoint", ""), body, headers, timeout)
        kind = config.get("tts_response_mode", "url")
        try:
            if kind == "binary":
                audio_extension(raw)
                return raw
            if kind not in ("url", "base64", "hex"):
                raise ValueError("invalid mode")
            value = extract_path(json.loads(raw), config.get("tts_audio_path", "output.audio.url"))
            if kind == "url":
                if not isinstance(value, str):
                    raise ValueError("invalid URL value")
                return await http.download_audio(value, timeout)
            return decode_audio(value, kind)
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError("invalid audio response mapping") from None

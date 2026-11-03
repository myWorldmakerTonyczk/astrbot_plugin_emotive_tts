"""Single-sentence test of the currently saved custom request configuration."""

import asyncio
import base64
import os

from astrbot.api.web import error_response, json_response, request

from ..custom_request import audio_extension
from ..director import resolve_instruction
from ..network import safe_error_message
from ..seed_lab import PRESETS, PreviewEvent


async def test_request(settings, schema, revision):
    body = await request.json(default=None)
    if not isinstance(body, dict):
        return error_response("请求格式错误。", status_code=400)
    plugin = settings.plugin
    async with settings.lock:
        if plugin.closing or body.get("revision") != revision(plugin.config):
            return error_response("配置已变化，请先重新加载并保存。", status_code=409)
        if getattr(settings, "test_running", False):
            return error_response("已有接口测试正在执行。", status_code=409)
        config = {**{k: v["default"] for k, v in schema.items()}, **dict(plugin.config)}
        if config.get("tts_mode") != "自定义兼容接口":
            return error_response("请先选择并保存自定义兼容接口。", status_code=400)
        settings.test_running = True
    job = asyncio.current_task()
    plugin.jobs.add(job)
    try:
        scene = PRESETS[0]
        trace = {}
        config["api_key"] = config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
        async with asyncio.timeout(float(config["job_timeout"])):
            async with plugin.gate.enter("custom-request-test"):
                instruction = await resolve_instruction(
                    config,
                    plugin.context,
                    plugin.http,
                    PreviewEvent(scene, config),
                    scene["reply"],
                    trace=trace,
                )
                data = await plugin.http.synthesize(config, scene["reply"], instruction)
                extension = audio_extension(data)
        return json_response(
            {
                "audio_base64": base64.b64encode(data).decode("ascii"),
                "mime_type": {
                    "wav": "audio/wav",
                    "mp3": "audio/mpeg",
                    "ogg": "audio/ogg",
                    "flac": "audio/flac",
                }[extension],
                "format": extension,
                "text": scene["reply"],
                "instruction": instruction,
                "source": trace.get("source"),
            },
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001 -- never return provider secrets
        return error_response(safe_error_message(exc), status_code=400)
    finally:
        settings.test_running = False
        plugin.jobs.discard(job)

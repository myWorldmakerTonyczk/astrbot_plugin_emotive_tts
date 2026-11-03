"""Authenticated Dashboard settings API; preserve existing config and mask secrets."""

import asyncio
import hashlib
import json
import math
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from ..emotion import validate_instruction
from ..network import headers_from_config, validate_url
from ..queue_manager import WorkGate

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "views/settings/schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SECRETS = {"api_key", "analysis_api_key", "tts_headers", "analysis_headers"}
EDITABLE = set(SCHEMA) - {"config_revision", "whitelist_revision", "umo_whitelist"}
LIMITS = {
    "speech_rate": (0.5, 2),
    "tts_seed": (-1, 65535),
    "trigger_probability": (0, 1),
    "context_messages": (0, 50),
    "context_max_chars": (0, 20000),
    "analysis_timeout": (1, 120),
    "tts_timeout": (1, 120),
    "job_timeout": (1, 600),
    "max_text_chars": (1, 10000),
    "concurrency": (1, 16),
    "queue_capacity": (1, 256),
    "audio_ttl_seconds": (300, 604800),
}


def revision(config) -> str:
    values = {key: config.get(key, item["default"]) for key, item in SCHEMA.items()}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def settings_payload(config) -> dict:
    values = {key: config.get(key, item["default"]) for key, item in SCHEMA.items()}
    configured = {key: bool(values[key] and values[key] != "{}") for key in SECRETS}
    for key in SECRETS:
        values[key] = ""
    for key in ("config_revision", "whitelist_revision", "umo_whitelist"):
        values.pop(key, None)
    return {
        "values": values,
        "configured": configured,
        "revision": revision(config),
        "schema": {
            key: item
            for key, item in SCHEMA.items()
            if key not in {"config_revision", "whitelist_revision", "umo_whitelist"}
        },
    }


def validate_changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or set(changes) - EDITABLE:
        raise ValueError("包含未知或不可在视图中修改的配置项。")
    result = {}
    for key, value in changes.items():
        item = SCHEMA[key]
        kind = item["type"]
        if kind == "bool" and type(value) is not bool:
            raise ValueError(f"{item['description']}必须是开关值。")
        if kind == "int" and type(value) is not int:
            raise ValueError(f"{item['description']}必须是整数。")
        if kind == "float" and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f"{item['description']}必须是有效数值。")
        if kind in ("string", "text") and (not isinstance(value, str) or len(value) > 16000):
            raise ValueError(f"{item['description']}必须是有效文本。")
        if kind == "list" and (
            not isinstance(value, list)
            or len(value) > 1000
            or any(not isinstance(v, str) or len(v) > 500 for v in value)
        ):
            raise ValueError("白名单格式错误，请每行填写一个完整UMO。")
        if key in ("private_umo_whitelist", "group_umo_whitelist"):
            kind = "FriendMessage" if key.startswith("private") else "GroupMessage"
            if any(
                len(umo.split(":")) != 3 or umo.split(":")[1] != kind or not all(umo.split(":"))
                for umo in value
            ):
                raise ValueError(f"{item['description']}请填写完整的{kind}类型UMO。")
        if "options" in item and value not in item["options"]:
            raise ValueError(f"{item['description']}选项无效。")
        if key in LIMITS and not LIMITS[key][0] <= value <= LIMITS[key][1]:
            low, high = LIMITS[key]
            raise ValueError(f"{item['description']}范围为{low}至{high}。")
        if key in SECRETS and not value:
            continue  # An empty masked input keeps the stored value.
        if key.endswith("headers"):
            try:
                headers_from_config(value)
            except (ValueError, TypeError, RuntimeError):
                raise ValueError("请求头须为合法JSON对象，不得包含换行或传输控制头。") from None
        if key.endswith("endpoint") and value:
            try:
                validate_url(value)
            except (ValueError, RuntimeError):
                raise ValueError("接口地址必须是公开的HTTPS地址。") from None
        if key == "voice_persona" and len(value) > 2000:
            raise ValueError("语音人设最多2000字，请只保留声音特点和说话习惯。")
        if key == "fallback_instruction":
            try:
                value = validate_instruction(value)
            except (ValueError, TypeError):
                raise ValueError("默认语气须为非空短指令，最多100加权字符。") from None
        result[key] = value
    return result


class SettingsView:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()

    def register(self):
        context = self.plugin.context
        prefix = "/astrbot_plugin_emotive_tts/settings"
        context.register_web_api(prefix, self.get, ["GET"], "读取语音设置")
        context.register_web_api(prefix + "/save", self.save, ["POST"], "保存语音设置")
        from .seed_preview import SeedPreviewView

        self.seed_preview = SeedPreviewView(self, SCHEMA, revision)
        self.seed_preview.register()

    def payload(self):
        result = settings_payload(self.plugin.config)
        providers = []
        context = getattr(self.plugin, "context", None)
        if context is not None and hasattr(context, "get_all_providers"):
            for provider in context.get_all_providers():
                meta = provider.meta()
                providers.append((meta.id, f"{meta.id} · {meta.model}"))
        field = result["schema"]["analysis_provider_id"] = dict(
            result["schema"]["analysis_provider_id"]
        )
        field["options"] = [""] + [item[0] for item in providers]
        field["labels"] = ["请选择已配置的聊天模型"] + [item[1] for item in providers]
        selected = self.plugin.config.get("analysis_provider_id", "")
        if selected and selected not in field["options"]:
            field["options"].append(selected)
            field["labels"].append(selected + "（当前不可用，请重新选择）")
        field = result["schema"]["tts_provider_id"] = dict(result["schema"]["tts_provider_id"])
        available = (
            context.get_all_tts_providers()
            if context is not None and hasattr(context, "get_all_tts_providers")
            else []
        )
        models = [(p.meta().id, p.meta().model) for p in available]
        field["options"] = [""] + [item[0] for item in models]
        field["labels"] = ["请选择已配置的语音模型"] + [
            f"{id} · {model or 'TTS'}" for id, model in models
        ]
        selected = self.plugin.config.get("tts_provider_id", "")
        if selected and selected not in field["options"]:
            field["options"].append(selected)
            field["labels"].append(selected + "（当前不可用）")
        return result

    async def get(self):
        return json_response(self.payload(), headers={"Cache-Control": "no-store"})

    async def save(self):
        body = await request.json(default=None)
        if not isinstance(body, dict):
            return error_response("请求内容必须是JSON对象。", status_code=400)
        async with self.lock:
            plugin = self.plugin
            if plugin.closing:
                return error_response("插件正在重载，请稍后刷新。", status_code=409)
            if body.get("revision") != revision(plugin.config):
                return error_response("设置已在其他页面更新，请重新加载后再保存。", status_code=409)
            try:
                changes = validate_changes(body.get("changes", {}))
                clear = body.get("clear_secrets", [])
                if not isinstance(clear, list) or any(key not in SECRETS for key in clear):
                    raise ValueError("清除凭据的请求无效。")
                for key in clear:
                    changes[key] = "{}" if key.endswith("headers") else ""
            except (ValueError, TypeError) as exc:
                return error_response(str(exc), status_code=400)
            queue_changed = any(
                key in changes and changes[key] != plugin.config.get(key, SCHEMA[key]["default"])
                for key in ("concurrency", "queue_capacity")
            )
            if queue_changed and plugin.gate.pending:
                return error_response(
                    "还有语音任务正在执行，请完成后再修改并发或队列容量。", status_code=409
                )
            before = dict(plugin.config)
            try:
                plugin.config.update(changes)
                plugin.config.save_config()
            except Exception as exc:  # noqa: BLE001 -- never expose stored credentials
                plugin.config.clear()
                plugin.config.update(before)
                logger.warning("[EmotiveTTS] 设置保存失败: %s", type(exc).__name__)
                return error_response(
                    "保存失败，设置未应用，请检查配置目录写入权限。", status_code=500
                )
            if queue_changed:
                plugin.gate = WorkGate(
                    plugin.config.get("concurrency", 2), plugin.config.get("queue_capacity", 16)
                )
            return json_response(self.payload(), headers={"Cache-Control": "no-store"})

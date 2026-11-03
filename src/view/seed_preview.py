"""Dashboard-authenticated APIs for persistent seed comparisons."""

import base64

from astrbot.api.web import error_response, json_response, request

from ..custom_request import audio_extension
from ..network import safe_error_message
from ..seed_lab import SeedLab


class SeedPreviewView:
    def __init__(self, settings, schema, revision):
        self.settings = settings
        self.plugin = settings.plugin
        self.revision = revision
        self.lab = SeedLab(self.plugin, {k: v["default"] for k, v in schema.items()})

    def register(self):
        prefix = "/astrbot_plugin_emotive_tts/settings/seed"
        for path, handler, method in (
            ("", self.listing, "GET"),
            ("/start", self.start, "POST"),
            ("/audio", self.audio, "POST"),
            ("/apply", self.apply, "POST"),
            ("/cancel", self.cancel, "POST"),
            ("/delete", self.delete, "POST"),
        ):
            self.plugin.context.register_web_api(prefix + path, handler, [method], "种子试听")

    def response(self):
        data = self.lab.listing()
        data["revision"] = self.revision(self.plugin.config)
        return json_response(data, headers={"Cache-Control": "no-store"})

    async def listing(self):
        return self.response()

    async def start(self):
        body = await request.json(default=None)
        if not isinstance(body, dict):
            return error_response("请求格式错误。", status_code=400)
        async with self.settings.lock:
            if self.plugin.closing:
                return error_response("插件正在重载。", status_code=409)
            if body.get("revision") != self.revision(self.plugin.config):
                return error_response("配置已变化，请重新加载设置后再生成。", status_code=409)
            try:
                self.lab.start(
                    self.plugin.config,
                    body.get("count", 3),
                    body["revision"],
                    seeds=body.get("seeds"),
                )
            except ValueError as exc:
                return error_response(str(exc), status_code=400)
            except Exception as exc:  # noqa: BLE001 -- do not expose provider credentials
                return error_response(safe_error_message(exc), status_code=400)
        return self.response()

    async def audio(self):
        body = await request.json(default=None)
        if not isinstance(body, dict):
            return error_response("请求格式错误。", status_code=400)
        try:
            data = self.lab.audio(body.get("batch_id"), body.get("sample_id"))
        except (ValueError, OSError):
            return error_response("音频不存在或尚未生成，请刷新。", status_code=404)
        return json_response(
            {
                "audio_base64": base64.b64encode(data).decode("ascii"),
                "mime_type": {
                    "wav": "audio/wav",
                    "mp3": "audio/mpeg",
                    "ogg": "audio/ogg",
                    "flac": "audio/flac",
                }[audio_extension(data)],
            }
        )

    async def apply(self):
        body = await request.json(default=None)
        if not isinstance(body, dict):
            return error_response("请求格式错误。", status_code=400)
        async with self.settings.lock:
            if self.plugin.closing or body.get("revision") != self.revision(self.plugin.config):
                return error_response("配置已变化或正在重载，请重新加载后再选择。", status_code=409)
            try:
                batch = self.lab.get(body.get("batch_id"))
                seed = body.get("seed")
                if type(seed) is not int or seed not in batch["seeds"]:
                    raise ValueError("请选择这批试听中的一个种子。")
                if not all(s["status"] == "ok" for s in batch["samples"] if s["seed"] == seed):
                    raise ValueError("这个种子的五种语境还没有全部生成成功。")
            except ValueError as exc:
                return error_response(str(exc), status_code=400)
            existed, previous = "tts_seed" in self.plugin.config, self.plugin.config.get("tts_seed")
            try:
                self.plugin.config["tts_seed"] = seed
                self.plugin.config.save_config()
            except Exception:  # noqa: BLE001 -- restore live config on failed persistence
                if existed:
                    self.plugin.config["tts_seed"] = previous
                else:
                    self.plugin.config.pop("tts_seed", None)
                return error_response("种子保存失败，原设置已保留。", status_code=500)
            batch["applied_seed"] = seed
            try:
                self.lab._store(batch)
            except OSError:
                pass  # Seed is saved; comparison metadata must not misreport failure.
        return self.response()

    async def cancel(self):
        return await self._action("cancel")

    async def delete(self):
        return await self._action("delete")

    async def _action(self, action):
        body = await request.json(default=None)
        if not isinstance(body, dict):
            return error_response("请求格式错误。", status_code=400)
        async with self.settings.lock:
            try:
                await getattr(self.lab, action)(body.get("batch_id"))
            except ValueError as exc:
                return error_response(str(exc), status_code=400)
            except OSError:
                return error_response("记录读写失败，请检查目录权限。", status_code=500)
        return self.response()

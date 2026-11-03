"""Coordinate AstrBot events, voice jobs and lifecycle cleanup.

Tone analysis, request validation and transport live in separate modules.
"""

import asyncio
import os
import random
import time
import uuid
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, StarTools

from .configuration import PLATFORM, migrate_mode, migrate_voice_style, migrate_whitelists
from .director import resolve_instruction
from .emotion import clean_for_speech, history_snapshot
from .events import DONE, SNAPSHOT, TOOL_SENT
from .network import HttpServices, safe_error_message
from .platform_tts import platform_connection_config
from .queue_manager import WorkGate
from .view.settings import SettingsView
from .voice_memory import VoiceMemory, supplement_request


class EmotiveTTSMixin:
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        migrated_mode = migrate_mode(config)
        migrated_whitelists = migrate_whitelists(config)
        migrated_voice = migrate_voice_style(config)
        if (migrated_mode or migrated_whitelists or migrated_voice) and hasattr(
            config, "save_config"
        ):
            config.save_config()
        self.config = config
        self.http = HttpServices()
        self.gate = WorkGate(
            max(1, int(config.get("concurrency", 2))), max(1, int(config.get("queue_capacity", 16)))
        )
        self.data_dir: Path = StarTools.get_data_dir("astrbot_plugin_emotive_tts") / "audio"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.voice_memory = VoiceMemory(self.data_dir.parent / "voice_memory.sqlite3")
        self.jobs: set[asyncio.Task[None]] = set()
        self.cleaner: asyncio.Task[None] | None = None
        self.closing = False

    async def initialize(self) -> None:
        self.settings_view = SettingsView(self)
        self.settings_view.register()
        self.cleaner = asyncio.create_task(self._cleanup_loop())

    def allowed(self, event: AstrMessageEvent) -> bool:
        if self.closing or not self.config.get("enabled", True):
            return False
        group = bool(event.get_group_id())
        if not self.config.get("group_enabled" if group else "private_enabled", True):
            return False
        key = "group_umo_whitelist" if group else "private_umo_whitelist"
        whitelist = self.config.get(key, self.config.get("umo_whitelist", []))
        return not whitelist or event.unified_msg_origin in whitelist

    async def capture(self, event: AstrMessageEvent, req) -> None:
        # Snapshot once before chat generation, not from a shared global buffer.
        if not self.allowed(event) or event.get_extra(SNAPSHOT) is not None:
            return
        original_persona = req.system_prompt or ""
        spoken = await supplement_request(self, event, req)
        event.set_extra(
            SNAPSHOT,
            {
                "conversation_id": getattr(getattr(req, "conversation", None), "cid", None),
                "history": history_snapshot(
                    req.contexts,
                    max(0, min(50, int(self.config.get("context_messages", 5)))),
                    max(0, min(20000, int(self.config.get("context_max_chars", 4000)))),
                ),
                "tool_history": history_snapshot(
                    req.contexts,
                    50,
                    max(0, min(20000, int(self.config.get("context_max_chars", 4000)))),
                )
                if self.config.get("tool_enabled", True)
                else [],
                "spoken_history": spoken,
                "user": event.get_message_str()[:2000],
                "persona": original_persona[:1500]
                if self.config.get("include_persona", True)
                else "",
            },
        )

    async def instruction(
        self,
        event: AstrMessageEvent,
        text: str,
        force_auto: bool = False,
        context_num: int | None = None,
    ) -> str:
        return await resolve_instruction(
            self.config,
            self.context,
            self.http,
            event,
            text,
            force_auto=force_auto,
            context_num=context_num,
        )

    async def _run_voice_job(
        self,
        event: AstrMessageEvent,
        text: str,
        explicit_instruction: str | None = None,
        force_auto: bool = False,
        context_num: int | None = None,
        speech_rate: float | None = None,
    ) -> None:
        """Track every voice job so plugin unload cancels it and releases its slot."""
        job = asyncio.create_task(
            self._generate_and_send(
                event, text, explicit_instruction, force_auto, context_num, speech_rate
            )
        )
        self.jobs.add(job)
        try:
            await job
        finally:
            self.jobs.discard(job)

    async def _generate_and_send(
        self,
        event: AstrMessageEvent,
        text: str,
        explicit_instruction: str | None = None,
        force_auto: bool = False,
        context_num: int | None = None,
        speech_rate: float | None = None,
    ) -> None:
        async with asyncio.timeout(float(self.config.get("job_timeout", 75))):
            async with self.gate.enter(event.unified_msg_origin):
                config = dict(self.config)
                if config.get("tts_mode") == PLATFORM:
                    config = platform_connection_config(self.context, config)
                instruction = (
                    explicit_instruction
                    if explicit_instruction is not None
                    else await self.instruction(
                        event, text, force_auto=force_auto, context_num=context_num
                    )
                )
                if speech_rate is not None:
                    config["speech_rate"] = speech_rate
                config["api_key"] = config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
                data = await self.http.synthesize(config, text, instruction)
                path = self.data_dir / (uuid.uuid4().hex + ".wav")
                await asyncio.to_thread(path.write_bytes, data)
                # Send before touching the original result: failure retains text.
                await event.send(
                    event.chain_result([Comp.Record.fromFileSystem(str(path), text=text)])
                )

    async def decorate(self, event: AstrMessageEvent) -> None:
        result = event.get_result()
        if (
            not self.allowed(event)
            or not self.config.get("auto_reply_voice", True)
            or event.get_extra(TOOL_SENT)
            or event.get_extra(DONE)
            or result is None
            or not result.is_llm_result()
        ):
            return
        # Streaming has already sent text; v0.1 deliberately leaves it unchanged.
        if "STREAMING" in str(result.result_content_type):
            return
        if any(isinstance(c, Comp.Record) for c in result.chain):
            return
        text = clean_for_speech("".join(c.text for c in result.chain if isinstance(c, Comp.Plain)))
        if not text or len(text) > int(self.config.get("max_text_chars", 600)):
            return
        if random.random() >= float(self.config.get("trigger_probability", 1.0)):
            return
        event.set_extra(DONE, True)
        try:
            await self._run_voice_job(event, text)
        except asyncio.CancelledError:
            if not self.closing:
                raise
            return
        except Exception as exc:  # noqa: BLE001 -- platform/API failures fall back to text
            logger.warning("[EmotiveTTS] 语音失败，保留文字: %s", safe_error_message(exc))
            return
        if not self.config.get("send_text_with_voice", True):
            result.chain = [
                c for c in result.chain if not isinstance(c, (Comp.Plain, Comp.At, Comp.Reply))
            ]

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                cutoff = time.time() - max(300, int(self.config.get("audio_ttl_seconds", 3600)))
                for path in self.data_dir.iterdir():
                    if path.suffix.lower() not in {
                        ".wav",
                        ".mp3",
                        ".ogg",
                        ".opus",
                        ".flac",
                        ".amr",
                        ".m4a",
                    }:
                        continue
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[EmotiveTTS] 临时音频清理失败")
            await asyncio.sleep(60)

    async def terminate(self) -> None:
        self.closing = True
        tasks = [*self.jobs, *([self.cleaner] if self.cleaner else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.http.close()

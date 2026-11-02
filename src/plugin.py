import asyncio
import os
import random
import time
import uuid

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, StarTools

from .configuration import analysis_mode, migrate_mode
from .emotion import (
    DEFAULT_INSTRUCTION,
    DIRECTOR_PROMPT,
    analysis_payload,
    clean_for_speech,
    history_snapshot,
    parse_instruction,
    validate_instruction,
)
from .network import HttpServices
from .queue_manager import WorkGate

SNAPSHOT = "emotive_tts_snapshot"
DONE = "emotive_tts_done"
TOOL_SENT = "emotive_tts_tool_sent"
TOOL_BUSY = "emotive_tts_tool_busy"


class EmotiveTTSMixin:
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        if migrate_mode(config) and hasattr(config, "save_config"):
            config.save_config()
        self.config = config
        self.http = HttpServices()
        self.gate = WorkGate(
            max(1, int(config.get("concurrency", 2))), max(1, int(config.get("queue_capacity", 16)))
        )
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_emotive_tts") / "audio"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = set()
        self.cleaner = None
        self.closing = False

    async def initialize(self):
        self.cleaner = asyncio.create_task(self._cleanup_loop())

    def allowed(self, event):
        if self.closing or not self.config.get("enabled", True):
            return False
        group = bool(event.get_group_id())
        if not self.config.get("group_enabled" if group else "private_enabled", True):
            return False
        whitelist = self.config.get("umo_whitelist", [])
        return not whitelist or event.unified_msg_origin in whitelist

    async def capture(self, event: AstrMessageEvent, req):
        # Snapshot once before chat generation, not from a shared global buffer.
        if not self.allowed(event) or event.get_extra(SNAPSHOT) is not None:
            return
        event.set_extra(
            SNAPSHOT,
            {
                "history": history_snapshot(
                    req.contexts,
                    max(0, min(50, int(self.config.get("context_messages", 5)))),
                    max(0, min(20000, int(self.config.get("context_max_chars", 4000)))),
                ),
                "user": event.get_message_str()[:2000],
                "persona": (req.system_prompt or "")[:1500]
                if self.config.get("include_persona", True)
                else "",
            },
        )

    async def instruction(self, event, text, force_auto=False):
        fallback = self.config.get("fallback_instruction", DEFAULT_INSTRUCTION)
        try:
            fallback = validate_instruction(fallback)
        except (TypeError, ValueError):
            fallback = DEFAULT_INSTRUCTION
        if not force_auto and not self.config.get("auto_emotion", True):
            return fallback
        snap = event.get_extra(SNAPSHOT) or {}
        payload = analysis_payload(
            snap.get("history", []),
            snap.get("user", ""),
            text,
            self.config.get("voice_style", ""),
            snap.get("persona", ""),
        )
        try:
            async with asyncio.timeout(float(self.config.get("analysis_timeout", 12))):
                if analysis_mode(self.config) == "external":
                    raw = await self.http.analyze_external(self.config, DIRECTOR_PROMPT, payload)
                else:
                    provider_id = self.config.get("analysis_provider_id", "")
                    provider = (
                        self.context.get_provider_by_id(provider_id)
                        if provider_id
                        else await self.context.get_using_provider_async(event.unified_msg_origin)
                    )
                    if provider is None:
                        raise ValueError("no analysis provider")
                    response = await provider.text_chat(
                        prompt=payload,
                        contexts=[],
                        system_prompt=DIRECTOR_PROMPT,
                        func_tool=None,
                        request_max_retries=0,
                    )
                    raw = response.completion_text
                return parse_instruction(raw)
        except Exception as exc:  # noqa: BLE001 -- provider errors must preserve chat
            # Never log exception text: SDK errors may contain keys or chat content.
            logger.warning("[EmotiveTTS] 情绪分析回退: %s", type(exc).__name__)
            return fallback

    async def _generate_and_send(self, event, text, explicit_instruction=None, force_auto=False):
        async with asyncio.timeout(float(self.config.get("job_timeout", 75))):
            async with self.gate.enter(event.unified_msg_origin):
                instruction = (
                    explicit_instruction
                    if explicit_instruction is not None
                    else await self.instruction(event, text, force_auto=force_auto)
                )
                config = dict(self.config)
                config["api_key"] = config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
                data = await self.http.synthesize(config, text, instruction)
                path = self.data_dir / (uuid.uuid4().hex + ".wav")
                await asyncio.to_thread(path.write_bytes, data)
                # Send before touching the original result: failure retains text.
                await event.send(
                    event.chain_result([Comp.Record.fromFileSystem(str(path), text=text)])
                )

    async def send_voice(
        self, event: AstrMessageEvent, text: str, mode: str = "auto", instruction: str = ""
    ) -> str:
        """将文字合成为语音并发送到当前会话。每次用户消息最多成功发送一次。

        auto模式使用最近配置条数的聊天上下文分析语气；manual模式直接使用instruction，
        不调用语气分析模型。成功后不要再次调用或重复朗读同一段文字。

        Args:
            text(string): 要朗读的正文，非空且不超过插件字数限制。
            mode(string): 可选，auto为上下文自动分析，manual为直接指定语气，默认auto。
            instruction(string): 可选，auto时留空；manual时必填简短中文语气指令，约50个汉字内，例如先轻快地打招呼，再放柔声音关心。
        """
        if not self.allowed(event) or not self.config.get("tool_enabled", True):
            return "语音工具在当前会话未启用，未发送语音。请用文字回复。"
        if event.get_extra(TOOL_SENT):
            return "本次消息的语音已发送，无需再次调用。"
        if event.get_extra(TOOL_BUSY):
            return "本次消息的语音正在生成，请勿重复调用。"
        if not isinstance(text, str) or not isinstance(instruction, str):
            return "参数错误：text和instruction必须是文字，未发送语音。"
        text = clean_for_speech(text)
        if not text or len(text) > int(self.config.get("max_text_chars", 600)):
            return "正文为空或超过配置的字数限制，未发送语音。请缩短正文。"
        if mode not in ("auto", "manual"):
            return "参数错误：mode只能是auto或manual，未发送语音。"
        explicit = None
        if mode == "manual":
            try:
                explicit = validate_instruction(instruction)
            except (TypeError, ValueError):
                return "manual模式需要有效instruction：非空、约50汉字内、不含尖括号或花括号。未发送语音。"
        elif instruction.strip():
            return "auto模式请将instruction留空；要直接指定语气请选择manual。未发送语音。"
        event.set_extra(TOOL_BUSY, True)
        job = asyncio.create_task(
            self._generate_and_send(event, text, explicit_instruction=explicit, force_auto=True)
        )
        self.jobs.add(job)
        try:
            await job
            event.set_extra(TOOL_SENT, True)
            return "语音已成功发送到当前会话。无需重复调用，也无需再次朗读同一段文字。"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- return safe feedback to the chat model
            logger.warning("[EmotiveTTS] 工具语音失败: %s", type(exc).__name__)
            return "语音生成或发送失败，未确认送达。请用文字回复，不要声称已发送语音。"
        finally:
            event.set_extra(TOOL_BUSY, False)
            self.jobs.discard(job)

    async def decorate(self, event: AstrMessageEvent):
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
        job = asyncio.create_task(self._generate_and_send(event, text))
        self.jobs.add(job)
        try:
            await job
        except asyncio.CancelledError:
            if not self.closing:
                raise
            return
        except Exception as exc:  # noqa: BLE001 -- platform/API failures fall back to text
            logger.warning("[EmotiveTTS] 语音失败，保留文字: %s", type(exc).__name__)
            return
        finally:
            self.jobs.discard(job)
        if not self.config.get("send_text_with_voice", True):
            result.chain = [
                c for c in result.chain if not isinstance(c, (Comp.Plain, Comp.At, Comp.Reply))
            ]

    async def _cleanup_loop(self):
        while True:
            try:
                cutoff = time.time() - max(300, int(self.config.get("audio_ttl_seconds", 3600)))
                for path in self.data_dir.glob("*.wav"):
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[EmotiveTTS] 临时音频清理失败")
            await asyncio.sleep(60)

    async def terminate(self):
        self.closing = True
        tasks = [*self.jobs, *([self.cleaner] if self.cleaner else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.http.close()

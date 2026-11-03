"""Resolve tone through AstrBot or a compatible analysis API, with safe fallback."""

import asyncio
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from .configuration import analysis_mode
from .emotion import (
    DEFAULT_INSTRUCTION,
    DEFAULT_VOICE_PERSONA,
    DIRECTOR_PROMPT,
    analysis_payload,
    history_snapshot,
    parse_instruction,
    validate_instruction,
)
from .events import SNAPSHOT
from .network import HttpServices, safe_error_message


async def resolve_instruction(
    config: Mapping[str, Any],
    context: Context,
    http: HttpServices,
    event: AstrMessageEvent,
    text: str,
    force_auto: bool = False,
    context_num: int | None = None,
    trace: dict[str, str] | None = None,
) -> str:
    debug = config.get("analysis_debug", True)
    fallback = config.get("fallback_instruction", DEFAULT_INSTRUCTION)
    try:
        fallback = validate_instruction(fallback)
    except (TypeError, ValueError):
        fallback = DEFAULT_INSTRUCTION
    if trace is not None:
        trace.update(source="default")
    if not force_auto and not config.get("auto_emotion", True):
        if debug:
            logger.info("[EmotiveTTS][调试] 分析关闭，使用默认instruction: %s", fallback)
        return fallback
    snap = event.get_extra(SNAPSHOT) or {}
    history = snap.get("history", [])
    if context_num is not None:
        history = history_snapshot(
            snap.get("tool_history", history),
            context_num,
            max(0, min(20000, int(config.get("context_max_chars", 4000)))),
        )
    count = (
        context_num
        if context_num is not None
        else max(0, min(50, int(config.get("context_messages", 5))))
    )
    remaining = max(0, min(20000, int(config.get("context_max_chars", 4000)))) - sum(
        len(item["content"]) for item in history
    )
    spoken = history_snapshot(
        [{"role": "assistant", "content": item["text"]} for item in snap.get("spoken_history", [])],
        count,
        max(0, remaining),
    )
    payload = analysis_payload(
        history,
        snap.get("user", ""),
        text,
        snap.get("persona", ""),
        spoken_history=spoken,
        voice_persona=config.get("voice_persona", DEFAULT_VOICE_PERSONA),
    )
    if debug:
        logger.info("[EmotiveTTS][调试] 发给LLM的系统提示词:\n%s", DIRECTOR_PROMPT)
        logger.info("[EmotiveTTS][调试] 发给LLM的上下文与正文:\n%s", payload)
    try:
        async with asyncio.timeout(float(config.get("analysis_timeout", 12))):
            if analysis_mode(config) == "external":
                raw = await http.analyze_external(config, DIRECTOR_PROMPT, payload)
            else:
                provider_id = config.get("analysis_provider_id", "")
                if not provider_id:
                    raise ValueError("select an analysis provider first")
                response = await context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=payload,
                    contexts=[],
                    system_prompt=DIRECTOR_PROMPT,
                    tools=None,
                    request_max_retries=1,
                )
                raw = response.completion_text
            if debug:
                logger.info("[EmotiveTTS][调试] LLM返回:\n%s", raw)
            instruction = parse_instruction(raw)
            if trace is not None:
                trace.update(source="llm")
            if debug:
                logger.info("[EmotiveTTS][调试] 最终instruction: %s", instruction)
            return instruction
    except Exception as exc:  # noqa: BLE001 -- provider errors must preserve chat
        # Never log exception text: SDK errors may contain keys or chat content.
        if trace is not None:
            trace.update(source="fallback", error=safe_error_message(exc))
        logger.warning("[EmotiveTTS] 情绪分析回退: %s", safe_error_message(exc))
        if debug:
            logger.info("[EmotiveTTS][调试] 回退instruction: %s", fallback)
        return fallback

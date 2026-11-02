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
    DIRECTOR_PROMPT,
    analysis_payload,
    history_snapshot,
    parse_instruction,
    validate_instruction,
)
from .events import SNAPSHOT
from .network import HttpServices


async def resolve_instruction(
    config: Mapping[str, Any],
    context: Context,
    http: HttpServices,
    event: AstrMessageEvent,
    text: str,
    force_auto: bool = False,
    context_num: int | None = None,
) -> str:
    fallback = config.get("fallback_instruction", DEFAULT_INSTRUCTION)
    try:
        fallback = validate_instruction(fallback)
    except (TypeError, ValueError):
        fallback = DEFAULT_INSTRUCTION
    if not force_auto and not config.get("auto_emotion", True):
        return fallback
    snap = event.get_extra(SNAPSHOT) or {}
    history = snap.get("history", [])
    if context_num is not None:
        history = history_snapshot(
            snap.get("tool_history", history),
            context_num,
            max(0, min(20000, int(config.get("context_max_chars", 4000)))),
        )
    payload = analysis_payload(
        history,
        snap.get("user", ""),
        text,
        config.get("voice_style", ""),
        snap.get("persona", ""),
    )
    try:
        async with asyncio.timeout(float(config.get("analysis_timeout", 12))):
            if analysis_mode(config) == "external":
                raw = await http.analyze_external(config, DIRECTOR_PROMPT, payload)
            else:
                provider_id = config.get("analysis_provider_id", "")
                provider = (
                    context.get_provider_by_id(provider_id)
                    if provider_id
                    else await context.get_using_provider_async(event.unified_msg_origin)
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

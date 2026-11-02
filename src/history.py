"""Insert tool speech at its tool-result position after framework persistence."""

import asyncio
import json

from astrbot.api import logger
from astrbot.core.utils.session_lock import session_lock_manager

from .emotion import text_content
from .events import SNAPSHOT


def insert_voice_at_tool_result(history: list[dict], text: str, marker: str) -> bool:
    """Preserve tool-result batches, then place speech before subsequent replies."""
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.get("role") != "tool" or marker not in text_content(message.get("content")):
            continue
        position = index + 1
        # A parallel tool-call batch must retain all matching tool results before
        # any new assistant message, including tools other than this plugin.
        while position < len(history) and history[position].get("role") == "tool":
            position += 1
        if position < len(history):
            following = history[position]
            if (
                following.get("role") == "assistant"
                and text_content(following.get("content")) == text
            ):
                return False
        history.insert(position, {"role": "assistant", "content": text})
        return True
    # No anchor means this turn was not persisted. Never attach speech to an
    # older turn or simply append it after unrelated newer conversation content.
    logger.warning("[EmotiveTTS] 未找到本次语音工具记录，未插入正文")
    return False


async def _append_voice_history(manager, umo: str, cid: str, text: str, marker: str) -> None:
    try:
        # The main Agent holds this same lock until its own history is saved.
        # Never await this job from inside the tool, which would deadlock.
        async with session_lock_manager.acquire_lock(umo):
            conversation = await manager.get_conversation(umo, cid)
            if conversation is None:
                logger.warning("[EmotiveTTS] 原对话已删除，未写入语音正文")
                return
            history = json.loads(conversation.history or "[]")
            if not isinstance(history, list):
                raise ValueError("invalid conversation history")
            if not insert_voice_at_tool_result(history, text, marker):
                return
            await manager.update_conversation(umo, cid, history=history)
    except Exception as exc:  # noqa: BLE001 -- sent speech must not be retried on storage failure
        logger.warning("[EmotiveTTS] 语音正文存入历史失败: %s", type(exc).__name__)


async def queue_voice_history(plugin, event, text: str, marker: str) -> bool:
    """Resolve the original conversation now, then schedule its serialized append."""
    try:
        manager = plugin.context.conversation_manager
        snapshot = event.get_extra(SNAPSHOT) or {}
        cid = snapshot.get("conversation_id")
        if not cid:
            cid = await manager.get_curr_conversation_id(event.unified_msg_origin)
        if not cid:
            logger.warning("[EmotiveTTS] 当前没有可写入的对话，语音正文未另存")
            return False
        job = asyncio.create_task(
            _append_voice_history(manager, event.unified_msg_origin, cid, text, marker)
        )
        plugin.jobs.add(job)
        job.add_done_callback(plugin.jobs.discard)
        return True
    except Exception as exc:  # noqa: BLE001 -- distinguish storage failure from delivery failure
        logger.warning("[EmotiveTTS] 无法安排语音历史保存: %s", type(exc).__name__)
        return False

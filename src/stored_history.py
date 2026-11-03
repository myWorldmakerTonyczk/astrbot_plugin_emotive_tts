"""Read saved conversation history rather than decorated provider contexts."""

import json

from astrbot.api import logger


async def read_stored_history(context, event, req):
    conversation = getattr(req, "conversation", None)
    cid = getattr(conversation, "cid", None)
    try:
        if conversation is None or not hasattr(conversation, "history"):
            manager = getattr(context, "conversation_manager", None)
            if manager is None:
                logger.warning(
                    "[EmotiveTTS] conversation.history不可用，使用空历史，不回退req.contexts"
                )
                return [], cid
            if not cid:
                cid = await manager.get_curr_conversation_id(event.unified_msg_origin)
            if not cid:
                return [], None
            conversation = await manager.get_conversation(event.unified_msg_origin, cid)
        raw = getattr(conversation, "history", "")
        history = json.loads(raw) if raw else []
        if not isinstance(history, list):
            raise ValueError("history must be a list")
        return history, cid
    except Exception as exc:  # noqa: BLE001 -- missing history must not stop voice
        logger.warning(
            "[EmotiveTTS] conversation.history读取失败(%s)，使用空历史，不回退req.contexts",
            type(exc).__name__,
        )
        return [], cid

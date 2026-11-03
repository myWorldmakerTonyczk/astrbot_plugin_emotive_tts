"""LLM voice tool: registration, public argument descriptions and execution."""

import asyncio
import uuid
from functools import wraps

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter

from ..events import TOOL_BUSY, TOOL_SENT
from ..history import queue_voice_history
from ..network import safe_error_message
from ..requests import ToolInputError, VoiceRequest
from ..voice_memory import remember_success


async def _send_voice(
    self,
    event: AstrMessageEvent,
    text: str,
    mode: str = "auto",
    instruction: str = "",
    context_num: int | None = None,
    speech_rate: float | None = None,
) -> str:
    """Shared validation, task tracking and delivery for both voice tools."""
    if not self.allowed(event) or not self.config.get("tool_enabled", True):
        return "语音工具在当前会话未启用，未发送语音。请用文字回复。"
    if event.get_extra(TOOL_SENT):
        return "本次消息的语音已发送，无需再次调用。"
    if event.get_extra(TOOL_BUSY):
        return "本次消息的语音正在生成，请勿重复调用。"
    try:
        request = VoiceRequest.from_tool(
            text, mode, instruction, int(self.config.get("max_text_chars", 600))
        )
    except ToolInputError as exc:
        return str(exc)
    event.set_extra(TOOL_BUSY, True)
    try:
        await self._run_voice_job(
            event,
            request.text,
            explicit_instruction=request.instruction,
            force_auto=True,
            context_num=context_num,
            speech_rate=speech_rate,
        )
        event.set_extra(TOOL_SENT, True)
        marker = f"[voice-memory:{uuid.uuid4().hex}]"
        remembered = await remember_success(self, event, request.text, marker)
        queued = await queue_voice_history(self, event, request.text, marker)
        memory = (
            "已安排按工具执行位置将正文保存为assistant消息，位于后续文字回复之前。"
            if queued
            else "正文未能安排单独保存到历史，请勿重复发送语音。"
        )
        if remembered:
            memory = (
                memory if queued else ""
            ) + "正文已保存为语音补充记忆，下次普通聊天会按需补充，无需再调用工具。"
        return f"语音已成功发送。{memory}无需重复调用或重复朗读。已说出的正文：{request.text}\n{marker}"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- return safe feedback to the chat model
        logger.warning("[EmotiveTTS] 工具语音失败: %s", safe_error_message(exc))
        return "语音生成或发送失败，未确认送达。请用文字回复，不要声称已发送语音。"
    finally:
        event.set_extra(TOOL_BUSY, False)


async def send_voice_auto(self, event: AstrMessageEvent, text: str, context_num: int = 5) -> str:
    """发送语音，自动结合当前聊天上文分析语气。无需自行提供语气指令。

    发送成功后会将正文作为assistant消息加入原对话历史，按工具执行位置排在后续文字回复之前；
    同时持久保存少量正文作补充记忆，下次普通聊天按需读取。
    不保存音频或instruction。发送失败不记录正文，无需模型另行保存或复述。
    每次用户消息最多成功发送一条语音；成功后不要重复调用任一语音工具。

    Args:
        text(string): 要朗读的正文，非空且不超过插件字数限制。
        context_num(number): 可选，最近的上文消息条数，必须为0至30的整数，默认5；0不传历史，当前用户输入和正文另传。
    """
    if (
        isinstance(context_num, bool)
        or not isinstance(context_num, int)
        or not 0 <= context_num <= 30
    ):
        return "参数错误：context_num必须是0至30的整数，未发送语音。"
    return await _send_voice(self, event, text, "auto", "", context_num=context_num)


async def send_voice_manual(
    self, event: AstrMessageEvent, text: str, instruction: str, speech_rate: float = 1.12
) -> str:
    """按指定instruction发送语音，跳过额外的语气分析模型调用。(推荐使用send_voice_auto)

    根据对话自行判断语气，填写简短具体的表达指导。传text、instruction，可选speech_rate，不需要context_num。
    发送成功后会将正文作为assistant消息加入原对话历史，按工具执行位置排在后续文字回复之前；
    同时持久保存少量正文作补充记忆，下次普通聊天按需读取。
    不保存音频或instruction。发送失败不记录正文，无需模型另行保存或复述。
    每次用户消息最多成功发送一条语音；成功后不要重复调用任一语音工具。

    Args:
        text(string): 要朗读的正文，非空且不超过插件字数限制。
        instruction(string): 必填语气指令，约50汉字内，例如先俏皮地假装埋怨，再放柔声音关心。
        speech_rate(number): 可选，本次语速，默认1.12，范围0.5至2，不修改视图设置。
    """
    if type(speech_rate) not in (int, float) or not 0.5 <= speech_rate <= 2:
        return "参数错误：speech_rate必须是0.5至2的数值，未发送语音。"
    return await _send_voice(self, event, text, "manual", instruction, speech_rate=speech_rate)


def register_voice_tool(module_name: str, tool):
    """Bind each tool to the entry module for AstrBot reload/disable handling."""

    @wraps(tool)
    async def handler(self, event: AstrMessageEvent, *args, **arguments) -> str:
        if tool is send_voice_manual:
            # Some models copy the automatic tool's argument. It has no meaning
            # for explicit instructions, so tolerate it without changing schema.
            arguments.pop("context_num", None)
        return await tool(self, event, *args, **arguments)

    handler.__module__ = module_name
    return filter.llm_tool(name="emotive_" + tool.__name__)(handler)

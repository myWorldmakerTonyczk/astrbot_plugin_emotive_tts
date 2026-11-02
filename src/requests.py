"""Validate model-provided tool arguments before starting any paid request."""

from dataclasses import dataclass

from .emotion import clean_for_speech, validate_instruction


class ToolInputError(ValueError):
    """Safe, user-facing validation message without secrets or provider details."""


@dataclass(frozen=True, slots=True)
class VoiceRequest:
    text: str
    instruction: str | None = None

    @classmethod
    def from_tool(cls, text: str, mode: str, instruction: str, max_chars: int) -> "VoiceRequest":
        if not isinstance(text, str) or not isinstance(instruction, str):
            raise ToolInputError("参数错误：text和instruction必须是文字，未发送语音。")
        text = clean_for_speech(text)
        if not text or len(text) > max_chars:
            raise ToolInputError("正文为空或超过配置的字数限制，未发送语音。请缩短正文。")
        if mode not in ("auto", "manual"):
            raise ToolInputError("参数错误：mode只能是auto或manual，未发送语音。")
        if mode == "manual":
            try:
                return cls(text, validate_instruction(instruction))
            except (TypeError, ValueError):
                raise ToolInputError(
                    "manual模式需要有效instruction：非空、约50汉字内、不含尖括号或花括号。未发送语音。"
                ) from None
        if instruction.strip():
            raise ToolInputError(
                "auto模式请将instruction留空；要直接指定语气请选择manual。未发送语音。"
            )
        return cls(text)

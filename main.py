"""AstrBot plugin entry point; implementation lives in src/."""

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star

from .src.plugin import EmotiveTTSMixin


class EmotiveTTS(EmotiveTTSMixin, Star):
    @filter.on_llm_request()
    async def capture(self, event: AstrMessageEvent, req):
        return await super().capture(event, req)

    @filter.on_decorating_result()
    async def decorate(self, event: AstrMessageEvent):
        return await super().decorate(event)

    @filter.llm_tool(name="emotive_send_voice")
    async def send_voice(
        self, event: AstrMessageEvent, text: str, mode: str = "auto", instruction: str = ""
    ) -> str:
        """将文字合成为语音并发送到当前会话。每次用户消息最多成功发送一次。

        auto使用最近上下文分析语气；manual直接使用instruction，不调用语气分析模型。
        成功后不要再次调用或重复朗读同一段文字。

        Args:
            text(string): 要朗读的正文，非空且不超过插件字数限制。
            mode(string): 可选，auto为上下文自动分析，manual为直接指定语气，默认auto。
            instruction(string): 可选，auto时留空；manual时必填简短中文语气指令，约50个汉字内，例如先轻快地打招呼，再放柔声音关心。
        """
        return await super().send_voice(event, text, mode, instruction)

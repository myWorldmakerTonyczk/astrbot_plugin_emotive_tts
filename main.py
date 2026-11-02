"""AstrBot plugin entry point; implementation lives in src/."""

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star

from .src.plugin import EmotiveTTSMixin
from .src.tools.tool import register_voice_tool, send_voice_auto, send_voice_manual


class EmotiveTTS(EmotiveTTSMixin, Star):
    @filter.on_llm_request()
    async def capture(self, event: AstrMessageEvent, req):
        return await super().capture(event, req)

    @filter.on_decorating_result()
    async def decorate(self, event: AstrMessageEvent):
        return await super().decorate(event)

    send_voice_auto = register_voice_tool(__name__, send_voice_auto)
    send_voice_manual = register_voice_tool(__name__, send_voice_manual)

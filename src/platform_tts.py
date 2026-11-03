"""Read Aliyun provider connection settings without calling or mutating the provider."""

from urllib.parse import urlsplit

from .configuration import CUSTOM, DEFAULT_ENDPOINT, REGIONS
from .network import ServiceError


def platform_connection_config(context, config) -> dict:
    selected = config.get("tts_provider_id", "")
    provider = (
        next((p for p in context.get_all_tts_providers() if p.meta().id == selected), None)
        if selected
        else None
    )
    if provider is None:
        raise ServiceError("platform TTS provider is unavailable")
    source = provider.provider_config
    if source.get("type") != "dashscope_tts":
        raise ServiceError("unsupported platform TTS protocol")
    key = getattr(provider, "chosen_api_key", None) or source.get("api_key", "")
    if not isinstance(key, str) or not key.strip():
        raise ServiceError("voice API key is required")
    endpoint = (
        source.get("api_base")
        or source.get("base_url")
        or REGIONS.get(config.get("aliyun_region", "北京"), DEFAULT_ENDPOINT)
    )
    # A DashScope API root can be expanded; never send the key to an inferred
    # endpoint belonging to another service or protocol.
    if endpoint.rstrip("/").endswith("/api/v1"):
        endpoint = endpoint.rstrip("/") + "/services/audio/tts/SpeechSynthesizer"
    elif urlsplit(endpoint).path in ("", "/"):
        endpoint = endpoint.rstrip("/") + "/api/v1/services/audio/tts/SpeechSynthesizer"
    result = dict(config)
    result.update(
        tts_mode=CUSTOM,
        api_key=key.strip(),
        tts_endpoint=endpoint,
        tts_headers=source.get("custom_headers") or "{}",
        tts_model=source.get("model", ""),
        voice=source.get("dashscope_tts_voice", ""),
    )
    return result

"""Resolve the UI's two speech API modes without mixing their settings."""

ALIYUN = "阿里云"
CUSTOM = "自定义兼容接口"
PLATFORM = "平台已配置的语音模型"
DEFAULT_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
REGIONS = {
    "北京": DEFAULT_ENDPOINT,
    "新加坡（国际）": "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
}


def migrate_voice_style(config):
    """Fold the removed voice-style field into the voice persona once."""
    if "voice_style" not in config:
        return False
    from .emotion import DEFAULT_VOICE_PERSONA

    style = str(config.pop("voice_style") or "").strip()
    persona = config.get("voice_persona", DEFAULT_VOICE_PERSONA)
    if style and style not in persona:
        config["voice_persona"] = (persona.rstrip() + "\n" + style).strip()
    return True


def migrate_mode(config):
    """Preserve v0.1 custom URLs/headers on the first upgrade only."""
    if int(config.get("config_revision", 0)) >= 1:
        return False
    legacy_endpoint = config.get("tts_endpoint", DEFAULT_ENDPOINT)
    legacy_headers = str(config.get("tts_headers", "{}")).strip()
    if legacy_endpoint != DEFAULT_ENDPOINT or legacy_headers not in ("", "{}"):
        config["tts_mode"] = CUSTOM
    else:
        config.setdefault("tts_mode", ALIYUN)
    config["analysis_mode"] = {
        "astrbot": "使用AstrBot模型",
        "external": "自定义分析接口",
    }.get(config.get("analysis_mode"), config.get("analysis_mode", "使用AstrBot模型"))
    config["config_revision"] = 1
    return True


def speech_connection(config):
    mode = config.get("tts_mode")
    # Direct callers with old config remain compatible. New UI always sets mode.
    if mode is None:
        return config.get("tts_endpoint", DEFAULT_ENDPOINT), config.get("tts_headers", "{}")
    if mode in (ALIYUN, "aliyun"):
        region = config.get("aliyun_region", "北京")
        if region not in REGIONS:
            raise ValueError("unknown Aliyun region")
        workspace = config.get("aliyun_workspace", "").strip()
        return REGIONS[region], ({"X-DashScope-Workspace": workspace} if workspace else {})
    if mode in (CUSTOM, "custom"):
        return config.get("tts_endpoint", ""), config.get("tts_headers", "{}")
    raise ValueError("unknown speech API mode")


def analysis_mode(config):
    return {
        "使用AstrBot模型": "astrbot",
        "自定义分析接口": "external",
    }.get(config.get("analysis_mode"), config.get("analysis_mode", "astrbot"))


def migrate_whitelists(config):
    """Split old UMO restrictions without accidentally allowing an entire new scope."""
    if config.get("whitelist_revision", 0) >= 1:
        return False
    old = config.get("umo_whitelist", [])
    for scope, kind in [("private", "FriendMessage"), ("group", "GroupMessage")]:
        key = scope + "_umo_whitelist"
        entries = [umo for umo in old if f":{kind}:" in umo]
        if not config.get(key) and old:
            config[key] = entries
            if not entries:
                config[scope + "_enabled"] = False
        else:
            config.setdefault(key, [])
    config["umo_whitelist"] = []
    config["whitelist_revision"] = 1
    return True

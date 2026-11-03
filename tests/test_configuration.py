import json
from pathlib import Path

from plugin_under_test.src.configuration import (
    ALIYUN,
    CUSTOM,
    DEFAULT_ENDPOINT,
    analysis_mode,
    migrate_mode,
    speech_connection,
)


def test_official_ignores_hidden_custom_fields():
    endpoint, headers = speech_connection(
        {
            "tts_mode": ALIYUN,
            "tts_endpoint": "https://other.test/",
            "tts_headers": '{"Authorization":"unwanted"}',
            "aliyun_workspace": "work",
        }
    )
    assert endpoint == DEFAULT_ENDPOINT
    assert headers == {"X-DashScope-Workspace": "work"}


def test_custom_and_region():
    config = {
        "tts_mode": CUSTOM,
        "tts_endpoint": "https://gateway.test/",
        "tts_headers": '{"X":"y"}',
    }
    assert speech_connection(config) == ("https://gateway.test/", '{"X":"y"}')
    assert (
        "dashscope-intl"
        in speech_connection({"tts_mode": ALIYUN, "aliyun_region": "新加坡（国际）"})[0]
    )
    assert analysis_mode({"analysis_mode": "自定义分析接口"}) == "external"


def test_old_config_migrates_once_and_preserves_choices():
    old = {"tts_endpoint": "https://gateway.test/", "tts_headers": "{}", "api_key": "secret"}
    assert migrate_mode(old) and old["tts_mode"] == CUSTOM and old["api_key"] == "secret"
    old["tts_mode"] = ALIYUN
    assert not migrate_mode(old) and old["tts_mode"] == ALIYUN
    fresh = {}
    migrate_mode(fresh)
    assert fresh["tts_mode"] == ALIYUN


def test_schema_dropdown_and_visibility():
    schema = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text())
    assert schema["tts_mode"]["options"] == [ALIYUN, CUSTOM, "平台已配置的语音模型"]
    assert schema["tts_headers"]["condition"] == {"tts_mode": CUSTOM}
    assert schema["tts_endpoint"]["condition"] == {"tts_mode": CUSTOM}
    assert schema["aliyun_region"]["condition"] == {"tts_mode": [ALIYUN, "平台已配置的语音模型"]}
    assert schema["analysis_endpoint"]["condition"] == {"analysis_mode": "自定义分析接口"}


def test_whitelist_migration_preserves_scope_restrictions():
    from plugin_under_test.src.configuration import migrate_whitelists

    config = {"umo_whitelist": ["bot:FriendMessage:1"]}
    assert migrate_whitelists(config)
    assert config["private_umo_whitelist"] == ["bot:FriendMessage:1"]
    assert config["group_enabled"] is False
    assert not migrate_whitelists(config)


def test_removed_style_migrates_into_persona_once():
    from plugin_under_test.src.configuration import migrate_voice_style

    config = {"voice_style": "轻声自然", "voice_persona": "女孩声线"}
    assert migrate_voice_style(config)
    assert config == {"voice_persona": "女孩声线\n轻声自然"}
    assert not migrate_voice_style(config)
    duplicate = {"voice_style": "自然", "voice_persona": "自然女孩声"}
    migrate_voice_style(duplicate)
    assert duplicate == {"voice_persona": "自然女孩声"}

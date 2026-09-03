"""配置层单元测试：v1.4 ElevenLabs 校验等。"""
from __future__ import annotations

import pytest

from podcast_ai.core.exceptions import ConfigError
from podcast_ai.core.models import AudioRenderConfig
from podcast_ai.infra.config import (
    AppConfig,
    ElevenLabsConfig,
    MiniMaxConfig,
    Settings,
    TTSConfig,
    require_elevenlabs_tts_config,
    require_minimax_tts_config,
)
from podcast_ai.infra.tts_client import (
    EdgeTTSSimpleClient,
    ElevenLabsTTSClient,
    MiniMaxTTSClient,
    get_default_tts_client,
)


def test_require_elevenlabs_rejects_wrong_provider() -> None:
    """非 elevenlabs provider 时不得使用 ElevenLabs 专用校验路径。"""
    tts = TTSConfig(provider="edge", elevenlabs=ElevenLabsConfig(api_key="k", voice_id="v"))
    with pytest.raises(ConfigError, match="elevenlabs"):
        require_elevenlabs_tts_config(tts)


def test_require_elevenlabs_ok_with_all_fields() -> None:
    """provider=elevenlabs 且 api_key、voice_id 齐全时返回归一化后的配置。"""
    tts = TTSConfig(
        provider="elevenlabs",
        elevenlabs=ElevenLabsConfig(
            api_key="sk_test",
            voice_id="voice_abc",
            model="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        ),
    )
    el = require_elevenlabs_tts_config(tts)
    assert el.api_key == "sk_test"
    assert el.voice_id == "voice_abc"
    assert el.model == "eleven_multilingual_v2"
    assert el.output_format == "mp3_44100_128"


def test_require_elevenlabs_api_key_fallback_from_tts_api_key() -> None:
    """elevenlabs.api_key 为空时可回退到 tts.api_key。"""
    tts = TTSConfig(
        provider="elevenlabs",
        api_key="fallback_key",
        elevenlabs=ElevenLabsConfig(api_key="", voice_id="vid"),
    )
    el = require_elevenlabs_tts_config(tts)
    assert el.api_key == "fallback_key"


def test_require_elevenlabs_missing_key_raises() -> None:
    """缺失 api_key / voice_id 时 ConfigError 信息明确。"""
    tts = TTSConfig(provider="elevenlabs", elevenlabs=ElevenLabsConfig(api_key="", voice_id=""))
    with pytest.raises(ConfigError, match="api_key"):
        require_elevenlabs_tts_config(tts)


def test_get_default_tts_client_elevenlabs() -> None:
    """v1.4：provider=elevenlabs 时返回 ElevenLabs 客户端。"""
    s = Settings(
        app=AppConfig(output_dir="."),
        tts=TTSConfig(
            provider="elevenlabs",
            elevenlabs=ElevenLabsConfig(api_key="test", voice_id="vid"),
        ),
    )
    client = get_default_tts_client(s)
    assert isinstance(client, ElevenLabsTTSClient)


def test_get_default_tts_client_edge() -> None:
    """provider=edge 时返回 Edge 客户端。"""
    s = Settings(
        app=AppConfig(output_dir="."),
        tts=TTSConfig(provider="edge", voice="zh-CN-XiaoxiaoNeural"),
    )
    client = get_default_tts_client(s)
    assert isinstance(client, EdgeTTSSimpleClient)


def test_require_minimax_ok_with_fallback_api_key() -> None:
    tts = TTSConfig(
        provider="minimax",
        api_key="fallback_mm_key",
        minimax=MiniMaxConfig(api_key="", model="speech-2.8-hd", voice_id="male-qn-qingse"),
    )
    mm = require_minimax_tts_config(tts)
    assert mm.api_key == "fallback_mm_key"
    assert mm.model == "speech-2.8-hd"


def test_get_default_tts_client_minimax() -> None:
    s = Settings(
        app=AppConfig(output_dir="."),
        tts=TTSConfig(
            provider="minimax",
            minimax=MiniMaxConfig(api_key="k", model="speech-2.8-hd", voice_id="male-qn-qingse"),
        ),
    )
    client = get_default_tts_client(s)
    assert isinstance(client, MiniMaxTTSClient)


def test_v14_ttsconfig_default_provider_is_elevenlabs() -> None:
    """v1.4：未显式写 provider 时默认 elevenlabs（与 PRD 主路径一致）。"""
    assert TTSConfig().provider == "elevenlabs"


def test_v14_get_default_client_elevenlabs_with_factory_tts_only() -> None:
    """仅用默认 TTSConfig 工厂时，get_default_tts_client 返回 ElevenLabs 实现。"""
    s = Settings(app=AppConfig(output_dir="."), tts=TTSConfig())
    assert isinstance(get_default_tts_client(s), ElevenLabsTTSClient)


def test_v20_audio_render_config_voice_music_crossfade_default() -> None:
    """v2.0 Task 01：AudioRenderConfig 含 voice_music_crossfade_seconds 默认值。"""
    c = AudioRenderConfig()
    assert c.voice_music_crossfade_seconds == 3.0
    assert c.crossfade_seconds == 8.0


def test_per_track_normalize_enabled_default_true() -> None:
    """默认开启每轨 normalize，与历史行为一致；可在配置中关闭。"""
    assert AudioRenderConfig().per_track_normalize_enabled is True
    from podcast_ai.infra.config import AudioConfig

    assert AudioConfig().per_track_normalize_enabled is True
    assert AudioConfig(per_track_normalize_enabled=False).per_track_normalize_enabled is False


def test_v14_conftest_settings_elevenlabs_minimal_fixture(settings_elevenlabs_minimal: Settings) -> None:
    """共享 fixture：合法 ElevenLabs 占位配置，供需注入 Settings 的用例复用。"""
    assert settings_elevenlabs_minimal.tts.provider == "elevenlabs"
    assert settings_elevenlabs_minimal.tts.elevenlabs.voice_id

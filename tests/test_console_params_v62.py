"""v6.2 Task 06：Console 选项目录与 Settings 映射校验（不启 Gradio）。

覆盖：audio 四字段、orch 纠正、LLM/TTS patch、非法组合中文错误、Preset 键。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_ai.console.option_catalogs import (
    base_url_for_interface,
    models_for_tts,
    provider_dropdown_choices,
    providers_for_model,
    voices_for_tts,
)
from podcast_ai.console.presets import PRESET_KEYS, build_snapshot
from podcast_ai.console.runner import ConsoleParams, run_plan, settings_from_params
from podcast_ai.core.exceptions import PodcastAIError
from podcast_ai.infra.config import (
    AppConfig,
    AudioConfig,
    ElevenLabsConfig,
    LLMConfig,
    MiniMaxConfig,
    Settings,
    TTSConfig,
    WebSearchConfig,
)


def _base(tmp_path: Path) -> Settings:
    return Settings(
        app=AppConfig(
            output_dir=str(tmp_path / "out"),
            music_dir=str(tmp_path / "music"),
            orchestration_mode="staged",
        ),
        llm=LLMConfig(model="old/model", base_url="https://example.invalid/v1"),
        audio=AudioConfig(
            per_track_normalize_enabled=True,
            voice_gain_db=0.0,
            voice_music_overlay_music_max_db=0.0,
            voice_music_post_overlay_ramp_seconds=0.0,
        ),
        tts=TTSConfig(
            provider="elevenlabs",
            elevenlabs=ElevenLabsConfig(model="eleven_multilingual_v2", voice_id="old-voice"),
            minimax=MiniMaxConfig(model="speech-2.8-hd", voice_id="male-qn-qingse"),
        ),
    )


def test_catalog_openrouter_base_url_and_gpt_providers() -> None:
    assert "openrouter.ai" in base_url_for_interface("openrouter")
    assert "azure/eu" in providers_for_model("openai/gpt-5.6-luna")
    assert providers_for_model("deepseek/deepseek-v3.2") == []
    labels = [label for label, _ in provider_dropdown_choices("openai/gpt-5.6-luna")]
    assert any("自动路由" in x for x in labels)
    with pytest.raises(ValueError, match="openrouter"):
        base_url_for_interface("anthropic")


def test_catalog_tts_presets() -> None:
    assert "eleven_v3" in models_for_tts("elevenlabs")
    assert "Fc5CaIGWKvLHapoOSM2K" in voices_for_tts("elevenlabs")
    assert "speech-2.8-hd" in models_for_tts("minimax")
    assert "Chinese (Mandarin)_Crisp_Girl" in voices_for_tts("minimax")
    assert models_for_tts("edge") == []


def test_audio_four_fields_map_into_settings(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(
            llm_model="deepseek/deepseek-v3.2",
            per_track_normalize_enabled=False,
            voice_gain_db=7.0,
            voice_music_overlay_music_max_db=-9.0,
            voice_music_post_overlay_ramp_seconds=0.7,
        ),
        base=_base(tmp_path),
    )
    assert updated.audio.per_track_normalize_enabled is False
    assert updated.audio.voice_gain_db == 7.0
    assert updated.audio.voice_music_overlay_music_max_db == -9.0
    assert updated.audio.voice_music_post_overlay_ramp_seconds == 0.7


def test_single_agent_forces_legacy_orchestration(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(
            agent_mode="single_agent",
            orchestration_mode="staged",
            llm_model="deepseek/deepseek-v3.2",
        ),
        base=_base(tmp_path),
    )
    assert updated.app.orchestration_mode == "legacy"


def test_multi_agent_keeps_staged(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(
            agent_mode="multi_agent",
            orchestration_mode="staged",
            llm_model="x",
        ),
        base=_base(tmp_path),
    )
    assert updated.app.orchestration_mode == "staged"


def test_invalid_orchestration_mode_raises_chinese(tmp_path: Path) -> None:
    with pytest.raises(PodcastAIError, match="staged / legacy"):
        settings_from_params(
            ConsoleParams(orchestration_mode="foobar", llm_model="x"),
            base=_base(tmp_path),
        )


def test_invalid_agent_mode_raises_chinese(tmp_path: Path) -> None:
    with pytest.raises(PodcastAIError, match="single_agent / multi_agent"):
        settings_from_params(
            ConsoleParams(agent_mode="both", llm_model="x"),
            base=_base(tmp_path),
        )


def test_llm_interface_sets_catalog_base_url(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(
            llm_interface="openrouter",
            llm_model="deepseek/deepseek-v3.2",
            llm_base_url="ignored",
            openrouter_provider="azure/eu",
        ),
        base=_base(tmp_path),
    )
    assert updated.llm.base_url == "https://openrouter.ai/api/v1"
    assert updated.llm.model == "deepseek/deepseek-v3.2"
    assert updated.llm.openrouter_provider == "azure/eu"


def test_invalid_llm_interface_raises(tmp_path: Path) -> None:
    with pytest.raises(PodcastAIError, match="模型接口"):
        settings_from_params(
            ConsoleParams(llm_interface="foo", llm_model="x"),
            base=_base(tmp_path),
        )


def test_tts_patch_only_when_provider_selected(tmp_path: Path) -> None:
    base = _base(tmp_path)
    keep = settings_from_params(
        ConsoleParams(tts_provider="default", tts_model="eleven_v3", tts_voice_id="should-ignore"),
        base=base,
    )
    assert keep.tts.elevenlabs.model == "eleven_multilingual_v2"
    assert keep.tts.elevenlabs.voice_id == "old-voice"

    patched = settings_from_params(
        ConsoleParams(
            tts_provider="elevenlabs",
            tts_model="eleven_v3",
            tts_voice_id="Fc5CaIGWKvLHapoOSM2K",
        ),
        base=base,
    )
    assert patched.tts.elevenlabs.model == "eleven_v3"
    assert patched.tts.elevenlabs.voice_id == "Fc5CaIGWKvLHapoOSM2K"

    mm = settings_from_params(
        ConsoleParams(
            tts_provider="minimax",
            tts_model="speech-2.8-hd",
            tts_voice_id="English_Sharp_Commentator",
        ),
        base=base,
    )
    assert mm.tts.minimax.voice_id == "English_Sharp_Commentator"


def test_invalid_tts_provider_raises_chinese(tmp_path: Path) -> None:
    with pytest.raises(PodcastAIError, match="tts_provider"):
        settings_from_params(
            ConsoleParams(tts_provider="azure", llm_model="x"),
            base=_base(tmp_path),
        )


def test_run_plan_missing_topic_or_model_returns_chinese_error(tmp_path: Path) -> None:
    """缺必填时在进入 pipeline 前失败，不调用真实 LLM。"""
    base = _base(tmp_path)
    with patch("podcast_ai.console.runner.plan_episode") as mocked:
        empty_topic = run_plan(
            ConsoleParams(topic="", llm_model="deepseek/deepseek-v3.2", output_dir=str(tmp_path)),
            settings=base,
        )
        assert empty_topic.status == "error"
        assert "主题" in empty_topic.error
        mocked.assert_not_called()

        empty_model = run_plan(
            ConsoleParams(topic="Night Drive", llm_model="", output_dir=str(tmp_path)),
            settings=base,
        )
        assert empty_model.status == "error"
        assert "模型" in empty_model.error
        mocked.assert_not_called()


def test_web_search_params_map_into_settings(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(
            llm_model="x",
            web_search_enabled=True,
            web_search_engine="exa",
            web_search_max_results=7,
            web_search_max_uses=3,
        ),
        base=_base(tmp_path),
    )
    assert updated.llm.web_search.enabled is True
    assert updated.llm.web_search.engine == "exa"
    assert updated.llm.web_search.max_results == 7
    assert updated.llm.web_search.max_uses == 3


def test_web_search_max_uses_zero_means_unlimited(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(llm_model="x", web_search_max_uses=0),
        base=_base(tmp_path),
    )
    assert updated.llm.web_search.max_uses is None


def test_web_search_toggle_maps_enabled_keeps_engine(tmp_path: Path) -> None:
    base = _base(tmp_path)
    base = base.model_copy(
        update={
            "llm": base.llm.model_copy(
                update={
                    "web_search": WebSearchConfig(enabled=False, engine="native", max_results=8),
                }
            )
        }
    )
    updated = settings_from_params(
        ConsoleParams(
            llm_model="x",
            web_search_enabled=True,
            web_search_engine="native",
            web_search_max_results=8,
        ),
        base=base,
    )
    assert updated.llm.web_search.enabled is True
    assert updated.llm.web_search.engine == "native"
    assert updated.llm.web_search.max_results == 8


def test_preset_keys_include_v62_fields() -> None:
    payload = build_snapshot(
        ConsoleParams(
            orchestration_mode="legacy",
            llm_interface="openrouter",
            tts_model="eleven_v3",
            tts_voice_id="abc",
            web_search_enabled=True,
            web_search_engine="native",
            web_search_max_results=5,
            debug=True,
            voice_gain_db=3.0,
        ).as_form_dict()
    )
    for key in (
        "orchestration_mode",
        "llm_interface",
        "web_search_enabled",
        "web_search_engine",
        "web_search_max_results",
        "web_search_max_uses",
        "debug",
        "tts_model",
        "tts_voice_id",
        "per_track_normalize_enabled",
        "voice_gain_db",
        "voice_music_overlay_music_max_db",
        "voice_music_post_overlay_ramp_seconds",
    ):
        assert key in PRESET_KEYS
        assert key in payload
    assert "api_key" not in payload

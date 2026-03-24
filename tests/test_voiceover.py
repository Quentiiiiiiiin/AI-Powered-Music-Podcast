"""Voiceover 模块：v1.3 边界时序 + v1.4 TTS 错误传递（mock TTS）。

Task 05：与 test_mixing / test_pipeline / test_tts_errors 一起覆盖「默认 ElevenLabs、
混音可消费 mp3、失败不静默、时序不回退」。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcast_ai.core.exceptions import TTSServiceError
from podcast_ai.core.models import (
    EpisodePlan,
    EpisodeSegment,
    PlaylistItem,
    SegmentBoundary,
)
from podcast_ai.infra.config import AppConfig, ElevenLabsConfig, Settings, TTSConfig
from podcast_ai.modules.voiceover.tts_service import VoiceoverService


def test_v13_insert_times_unchanged_with_mock_tts(tmp_path: Path) -> None:
    """segment_boundaries 存在时 insert_time 仍按 v1.3 规则，与 TTS 供应商无关。"""
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="a",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="hello",
                target_playlist=[PlaylistItem(segment_name="a", recommended_tracks=[], search_hints={})],
            ),
            EpisodeSegment(
                name="b",
                target_duration_seconds=300,
                bpm_range=(95, 115),
                mood="upbeat",
                host_script="world",
                target_playlist=[PlaylistItem(segment_name="b", recommended_tracks=[], search_hints={})],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="t",
        plan_id="p",
    )
    boundaries = [
        SegmentBoundary(music_start=0.0, music_end=150.0),
        SegmentBoundary(music_start=150.0, music_end=400.0),
    ]

    mock_tts = MagicMock()
    p1 = tmp_path / "a.mp3"
    p2 = tmp_path / "b.mp3"
    p1.write_bytes(b"x")
    p2.write_bytes(b"y")
    mock_tts.synthesize.side_effect = [p1, p2]

    settings = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(
            provider="elevenlabs",
            elevenlabs=ElevenLabsConfig(api_key="k", voice_id="v"),
        ),
    )
    svc = VoiceoverService(tts_client=mock_tts, settings=settings)
    vos = svc.generate_voiceovers(plan, segment_boundaries=boundaries)

    assert vos[0].insert_time_in_episode == 0.0
    assert vos[1].insert_time_in_episode == 150.0
    assert mock_tts.synthesize.call_count == 2


def test_v14_tts_podcast_error_propagates_not_silent(tmp_path: Path) -> None:
    """TTS 抛出 TTSServiceError 时不得静默改为占位，应向上传递。"""
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="a",
                target_duration_seconds=60,
                bpm_range=(90, 110),
                mood="chill",
                host_script="fail me",
                target_playlist=[PlaylistItem(segment_name="a", recommended_tracks=[], search_hints={})],
            ),
        ],
        target_duration_seconds=120,
        overall_bpm_range=(90, 120),
        style_description="t",
        plan_id="p",
    )
    mock_tts = MagicMock()
    mock_tts.synthesize.side_effect = TTSServiceError("quota")

    settings = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(provider="elevenlabs", elevenlabs=ElevenLabsConfig(api_key="k", voice_id="v")),
    )
    svc = VoiceoverService(tts_client=mock_tts, settings=settings)

    with pytest.raises(TTSServiceError, match="quota"):
        svc.generate_voiceovers(plan)

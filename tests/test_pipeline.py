"""流水线单元测试：plan_episode 在 mock LLM 下可跑通、create_episode 结构、v1.3/v1.4 验收点。"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_ai.core.exceptions import PlanMappingError, PodcastAIError
from podcast_ai.core.models import (
    EpisodePlan,
    EpisodeRequest,
    EpisodeSegment,
    PlaylistItem,
    SegmentBoundary,
)
from podcast_ai.core.pipeline import create_episode, load_plan_from_disk, plan_episode, save_plan_to_disk
from podcast_ai.infra.storage.paths import get_mix_output_path


def _mock_plan_json() -> str:
    """返回 LLM 模拟的 EpisodePlan JSON。"""
    return json.dumps(
        {
            "style_description": "Mock style",
            "overall_bpm_range": [90, 120],
            "segments": [
                {
                    "name": "开场",
                    "target_duration_seconds": 300,
                    "bpm_range": [90, 110],
                    "mood": "chill",
                    "host_script": "欢迎。",
                    "target_playlist": [
                        {"recommended_tracks": ["Track A"], "search_hints": {"genre": "chill"}},
                    ],
                },
            ],
        },
        ensure_ascii=False,
    )


@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan")
def test_plan_episode_with_mock_llm(mock_generate: object, tmp_path: Path) -> None:
    """plan_episode 在 mock LLM 下应正常完成并落盘。"""
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Mock style",
        plan_id="mock_plan",
    )
    mock_generate.return_value = plan

    request = EpisodeRequest(
        topic="Test",
        duration_minutes=10,
        language="zh",
        output_dir=tmp_path,
    )

    result_plan, plan_path, playlist_path = plan_episode(request)

    assert result_plan.plan_id
    assert plan_path.exists()
    assert plan_path.suffix == ".json"
    assert playlist_path.exists()
    assert "Test" in playlist_path.read_text(encoding="utf-8")


def test_save_and_load_plan_roundtrip(tmp_path: Path, sample_plan: EpisodePlan) -> None:
    """save_plan_to_disk / load_plan_from_disk 往返。"""
    from podcast_ai.infra.config import AppConfig, Settings

    settings = Settings(app=AppConfig(output_dir=str(tmp_path)))
    plan_path = save_plan_to_disk(sample_plan, settings=settings, episode_id="ep_test", plan_id="plan_test")
    loaded = load_plan_from_disk(plan_path)
    assert loaded.plan_id == sample_plan.plan_id
    assert len(loaded.segments) == len(sample_plan.segments)
    assert loaded.segments[0].name == sample_plan.segments[0].name


# ---------- v1.3 验收测试 ----------


def test_v13_voiceover_insert_time_from_actual_boundaries() -> None:
    """v1.3：串词 insert_time 基于 segment_boundaries 实际边界，而非 target_duration 累加。"""
    from podcast_ai.infra.config import AppConfig, Settings
    from podcast_ai.modules.voiceover.tts_service import VoiceoverService

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[PlaylistItem(segment_name="开场", recommended_tracks=[], search_hints={})],
            ),
            EpisodeSegment(
                name="主段",
                target_duration_seconds=300,
                bpm_range=(95, 115),
                mood="upbeat",
                host_script="",
                target_playlist=[PlaylistItem(segment_name="主段", recommended_tracks=[], search_hints={})],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test",
        plan_id="test",
    )
    # 实际边界：seg0 结束于 150s，seg1 结束于 400s（与 target_duration 300/300 不同）
    segment_boundaries = [
        SegmentBoundary(music_start=0.0, music_end=150.0),
        SegmentBoundary(music_start=150.0, music_end=400.0),
    ]
    settings = Settings(app=AppConfig(output_dir="./output"))
    svc = VoiceoverService(settings=settings)
    voiceovers = svc.generate_voiceovers(plan, segment_boundaries=segment_boundaries)
    assert len(voiceovers) == 2
    assert voiceovers[0].insert_time_in_episode == 0.0
    assert voiceovers[1].insert_time_in_episode == 150.0


@patch("podcast_ai.core.pipeline.LibraryScanner.scan_or_load_cache")
def test_v13_create_episode_mapping_failure_blocks(
    mock_scan: object,
    tmp_path: Path,
) -> None:
    """v1.3：segment 映射失败时 create_episode 抛出异常并阻断，不产生 mix 输出。"""
    from podcast_ai.core.models import Track, TrackMetadata, TrackWithMetadata
    from podcast_ai.infra.config import AppConfig, CacheConfig, Settings

    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    episode_id = "ep_test_unmapped"
    plan_id = "plan_test"
    episode_root = output_dir / "episodes" / episode_id
    plans_dir = episode_root / "plans"
    plans_dir.mkdir(parents=True)

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[
                    PlaylistItem(
                        segment_name="开场",
                        recommended_tracks=["NonExistentTrackXYZ"],
                        search_hints={},
                    )
                ],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test",
        plan_id=plan_id,
    )
    plan_path = save_plan_to_disk(
        plan,
        settings=Settings(app=AppConfig(output_dir=str(output_dir)), cache=CacheConfig(enabled=False)),
        episode_id=episode_id,
        plan_id=plan_id,
    )

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    mock_scan.return_value = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=music_dir / "other.wav", title="OtherSong", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=120.0, bpm=95.0, genre=None),
        ),
    ]

    mix_path = get_mix_output_path(episode_root, ext="wav")

    with pytest.raises((PlanMappingError, PodcastAIError)):
        create_episode(
            plan_path,
            music_dir,
            settings=Settings(
                app=AppConfig(output_dir=str(output_dir)),
                cache=CacheConfig(enabled=False),
            ),
        )

    assert not mix_path.exists()


# ---------- v1.4：供应商切换与注入点 ----------


def test_v14_create_episode_accepts_tts_client_parameter() -> None:
    """create_episode 支持注入 tts_client，便于 mock ElevenLabs 或回归单测。"""
    sig = inspect.signature(create_episode)
    assert "tts_client" in sig.parameters

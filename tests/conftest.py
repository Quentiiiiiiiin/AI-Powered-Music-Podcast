"""pytest 共享 fixtures。"""
from __future__ import annotations

from pathlib import Path

import pytest

from podcast_ai.core.models import (
    EpisodePlan,
    EpisodeSegment,
    PlaylistItem,
    Track,
    TrackMetadata,
    TrackWithMetadata,
)


@pytest.fixture
def sample_plan() -> EpisodePlan:
    """用于选曲/流水线测试的示例 EpisodePlan。"""
    return EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="欢迎收听。",
                target_playlist=[PlaylistItem(segment_name="开场", recommended_tracks=["Track A"], search_hints={})],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test style",
        plan_id="plan_test_1",
    )


@pytest.fixture
def sample_library() -> list[TrackWithMetadata]:
    """用于选曲测试的示例曲库（BPM 递增）。"""
    return [
        TrackWithMetadata(
            track=Track(id="t1", file_path=Path("a.mp3"), title="A", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=120.0, bpm=95.0, genre=None),
        ),
        TrackWithMetadata(
            track=Track(id="t2", file_path=Path("b.mp3"), title="B", artist="Y"),
            metadata=TrackMetadata(track_id="t2", duration_seconds=180.0, bpm=100.0, genre=None),
        ),
        TrackWithMetadata(
            track=Track(id="t3", file_path=Path("c.mp3"), title="C", artist="Z"),
            metadata=TrackMetadata(track_id="t3", duration_seconds=200.0, bpm=108.0, genre=None),
        ),
    ]

"""选曲模块单元测试：时长计算含 crossfade、BPM 排序稳定性、EpisodePlan 序列化。"""
from __future__ import annotations

from pathlib import Path

import pytest

from podcast_ai.core.models import EpisodePlan, EpisodeSegment, SelectedTrack, Track, TrackMetadata, TrackWithMetadata
from podcast_ai.modules.selection.selector import TrackSelector


def test_select_tracks_bpm_order(sample_plan: EpisodePlan, sample_library: list[TrackWithMetadata]) -> None:
    """选曲结果应按 BPM 升序。"""
    sel = TrackSelector(crossfade_seconds=8.0)
    result = sel.select_tracks(sample_plan, sample_library)
    bpms = [st.track.id for st in result]
    assert bpms == ["t1", "t2", "t3"]


def test_select_tracks_effective_duration_with_crossfade(
    sample_plan: EpisodePlan,
    sample_library: list[TrackWithMetadata],
) -> None:
    """有效时长计算：第一首全量，后续每首减去 crossfade。"""
    cf = 8.0
    sel = TrackSelector(crossfade_seconds=cf)
    result = sel.select_tracks(sample_plan, sample_library)
    assert len(result) >= 1
    assert result[0].effective_duration == 120.0
    if len(result) >= 2:
        assert result[1].effective_duration == 180.0 - cf
    if len(result) >= 3:
        assert result[2].effective_duration == 200.0 - cf
    total_effective = sum(st.effective_duration for st in result)
    expected = 120 + (180 - cf) + (200 - cf)
    assert abs(total_effective - expected) < 0.1


def test_select_tracks_timeline_continuity(
    sample_plan: EpisodePlan,
    sample_library: list[TrackWithMetadata],
) -> None:
    """相邻曲目时间线应连续（考虑 crossfade 重叠）。"""
    sel = TrackSelector(crossfade_seconds=8.0)
    result = sel.select_tracks(sample_plan, sample_library)
    for i in range(1, len(result)):
        prev_end = result[i - 1].end_time_in_episode
        curr_start = result[i].start_time_in_episode
        assert abs(curr_start - (prev_end - 8.0)) < 0.01


def test_select_tracks_empty_library(sample_plan: EpisodePlan) -> None:
    """空曲库应返回空列表。"""
    sel = TrackSelector()
    result = sel.select_tracks(sample_plan, [])
    assert result == []


def test_episode_plan_serialization_roundtrip(sample_plan: EpisodePlan) -> None:
    """EpisodePlan 可序列化/反序列化。"""
    json_str = sample_plan.model_dump_json(indent=2, ensure_ascii=False)
    loaded = EpisodePlan.model_validate_json(json_str)
    assert loaded.plan_id == sample_plan.plan_id
    assert len(loaded.segments) == len(sample_plan.segments)
    assert loaded.segments[0].name == sample_plan.segments[0].name
    assert loaded.overall_bpm_range == sample_plan.overall_bpm_range

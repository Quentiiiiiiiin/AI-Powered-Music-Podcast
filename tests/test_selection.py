"""选曲模块单元测试：时长计算含 crossfade、BPM 排序稳定性、EpisodePlan 序列化、v1.2 plan 驱动选曲。"""
from __future__ import annotations

from pathlib import Path

import pytest

from podcast_ai.core.exceptions import PlanMappingError
from podcast_ai.core.models import (
    EpisodePlan,
    EpisodeSegment,
    PlaylistItem,
    SelectedTrack,
    Stage2Script,
    Stage2Segment,
    Stage2Snapshot,
    Stage2SnapshotMeta,
    Track,
    TrackMetadata,
    TrackWithMetadata,
)
from podcast_ai.modules.selection.selector import TrackSelector, select_tracks_by_plan, select_tracks_by_snapshot


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


# ---------- v1.2 验收测试：plan 顺序保序、缺失映射报错 ----------


def test_v12_select_tracks_by_plan_order_strictly_follows_plan() -> None:
    """v1.2：selected_tracks 顺序严格等于 plan 顺序，不触发 BPM 二次排序。"""
    # plan 顺序：First -> Second；library 中 Second 的 BPM 更低，若按 BPM 排序会得到 Second, First
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[PlaylistItem(segment_name="开场", recommended_tracks=["First"], search_hints={})],
            ),
            EpisodeSegment(
                name="中段",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="up",
                host_script="",
                target_playlist=[PlaylistItem(segment_name="中段", recommended_tracks=["Second"], search_hints={})],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test",
        plan_id="plan_v12",
    )
    # BPM 与 plan 顺序相反：First 高 BPM，Second 低 BPM
    library = [
        TrackWithMetadata(
            track=Track(id="high", file_path=Path("first.mp3"), title="First", artist="A"),
            metadata=TrackMetadata(track_id="high", duration_seconds=120.0, bpm=108.0, genre=None),
        ),
        TrackWithMetadata(
            track=Track(id="low", file_path=Path("second.mp3"), title="Second", artist="B"),
            metadata=TrackMetadata(track_id="low", duration_seconds=100.0, bpm=92.0, genre=None),
        ),
    ]
    result = select_tracks_by_plan(plan, library, crossfade_seconds=8.0)
    # 必须按 plan 顺序：First -> Second，而非 BPM 排序
    assert [st.track.title for st in result] == ["First", "Second"]


def test_v12_select_tracks_by_plan_unmapped_raises_plan_mapping_error() -> None:
    """v1.2：plan 指定曲目无法映射时应抛出 PlanMappingError，且包含 segment/推荐曲目信息。"""
    unmapped_track = "QqWwRrTtYy_Unmppd"  # 不含 a/x，与 library(A/X/a.mp3) 无交集
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[
                    PlaylistItem(segment_name="开场", recommended_tracks=[unmapped_track], search_hints={}),
                ],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test",
        plan_id="plan_v12",
    )
    library = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=Path("a.mp3"), title="A", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=120.0, bpm=95.0, genre=None),
        ),
    ]
    with pytest.raises(PlanMappingError) as exc_info:
        select_tracks_by_plan(plan, library, crossfade_seconds=8.0)
    msg = str(exc_info.value)
    assert "segment" in msg.lower() or "Segment" in msg
    assert unmapped_track in msg or "推荐" in msg or "recommended" in msg.lower()


def test_v39_select_tracks_by_snapshot_follows_playlists_order() -> None:
    snapshot = Stage2Snapshot(
        schema="v3.0",
        meta=Stage2SnapshotMeta(
            request_id="r1",
            theme="t",
            language="zh-CN",
            target_duration_seconds=600,
        ),
        segments=[
            Stage2Segment(
                segment_id="seg_01",
                name="开场",
                target_duration_seconds=300,
                playlists=[
                    {"track": "First", "artist": "A"},
                    {"track": "Second", "artist": "B"},
                ],
                script=Stage2Script(segment_intro="", between_tracks=[]),
            ),
        ],
    )
    library = [
        TrackWithMetadata(
            track=Track(id="low", file_path=Path("second.mp3"), title="Second", artist="B"),
            metadata=TrackMetadata(track_id="low", duration_seconds=100.0, bpm=92.0, genre=None),
        ),
        TrackWithMetadata(
            track=Track(id="high", file_path=Path("first.mp3"), title="First", artist="A"),
            metadata=TrackMetadata(track_id="high", duration_seconds=120.0, bpm=108.0, genre=None),
        ),
    ]
    result = select_tracks_by_snapshot(snapshot, library, crossfade_seconds=8.0)
    assert [st.track.title for st in result] == ["First", "Second"]

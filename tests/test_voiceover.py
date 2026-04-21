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
    SelectedTrack,
    Stage2Script,
    Stage2Segment,
    Stage2Snapshot,
    Stage2SnapshotMeta,
    Track,
    TrackMetadata,
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


def test_v39_generate_voiceovers_from_snapshot_between_tracks(tmp_path: Path) -> None:
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
                playlists=[{"track": "A", "artist": "X"}, {"track": "B", "artist": "Y"}],
                script=Stage2Script(
                    segment_intro="intro",
                    between_tracks=[{"after_track_index": 0, "text": "between"}, {"after_track_index": 1, "text": None}],
                ),
            ),
        ],
    )
    selected_tracks = [[
        SelectedTrack(
            track=Track(id="t1", file_path=tmp_path / "a.wav", title="A", artist="X"),
            start_time_in_episode=0.0,
            end_time_in_episode=120.0,
            effective_duration=120.0,
        ),
        SelectedTrack(
            track=Track(id="t2", file_path=tmp_path / "b.wav", title="B", artist="Y"),
            start_time_in_episode=120.0,
            end_time_in_episode=240.0,
            effective_duration=120.0,
        ),
    ]]
    boundaries = [SegmentBoundary(music_start=0.0, music_end=240.0)]

    mock_tts = MagicMock()
    p1 = tmp_path / "intro.mp3"
    p2 = tmp_path / "between.mp3"
    p1.write_bytes(b"x")
    p2.write_bytes(b"y")
    mock_tts.synthesize.side_effect = [p1, p2]
    settings = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(provider="elevenlabs", elevenlabs=ElevenLabsConfig(api_key="k", voice_id="v")),
    )
    svc = VoiceoverService(tts_client=mock_tts, settings=settings)
    vos = svc.generate_voiceovers_from_snapshot(
        snapshot,
        selected_tracks_by_segment=selected_tracks,
        segment_boundaries=boundaries,
    )
    assert len(vos) == 2
    assert vos[0].text == "intro"
    assert vos[1].text == "between"
    assert vos[1].insert_time_in_episode == 120.0


def test_v39_segment_intro_after_previous_segment_end(tmp_path: Path) -> None:
    """方案 A：第 2 段 intro 锚定在上一段 music_end，而不是本段 music_start。"""
    snapshot = Stage2Snapshot(
        schema="v3.0",
        meta=Stage2SnapshotMeta(
            request_id="r2",
            theme="t",
            language="zh-CN",
            target_duration_seconds=900,
        ),
        segments=[
            Stage2Segment(
                segment_id="seg_01",
                name="段1",
                target_duration_seconds=500,
                playlists=[
                    {"track": "A", "artist": "X"},
                    {"track": "B", "artist": "Y"},
                    {"track": "C", "artist": "Z"},
                ],
                script=Stage2Script(
                    segment_intro="intro1",
                    between_tracks=[{"after_track_index": 1, "text": "after_t2"}],
                ),
            ),
            Stage2Segment(
                segment_id="seg_02",
                name="段2",
                target_duration_seconds=400,
                playlists=[{"track": "D", "artist": "W"}],
                script=Stage2Script(segment_intro="intro2", between_tracks=[]),
            ),
        ],
    )
    selected_tracks = [
        [
            SelectedTrack(
                track=Track(id="t1", file_path=tmp_path / "a.wav", title="A", artist="X"),
                start_time_in_episode=0.0,
                end_time_in_episode=120.0,
                effective_duration=120.0,
            ),
            SelectedTrack(
                track=Track(id="t2", file_path=tmp_path / "b.wav", title="B", artist="Y"),
                start_time_in_episode=100.0,
                end_time_in_episode=220.0,
                effective_duration=100.0,
            ),
            SelectedTrack(
                track=Track(id="t3", file_path=tmp_path / "c.wav", title="C", artist="Z"),
                start_time_in_episode=200.0,
                end_time_in_episode=360.0,
                effective_duration=140.0,
            ),
        ],
        [
            SelectedTrack(
                track=Track(id="t4", file_path=tmp_path / "d.wav", title="D", artist="W"),
                start_time_in_episode=330.0,
                end_time_in_episode=480.0,
                effective_duration=120.0,
            ),
        ],
    ]
    boundaries = [
        SegmentBoundary(music_start=0.0, music_end=360.0),
        SegmentBoundary(music_start=330.0, music_end=480.0),
    ]

    mock_tts = MagicMock()
    p1 = tmp_path / "intro1.mp3"
    p2 = tmp_path / "after_t2.mp3"
    p3 = tmp_path / "intro2.mp3"
    p1.write_bytes(b"1")
    p2.write_bytes(b"2")
    p3.write_bytes(b"3")
    mock_tts.synthesize.side_effect = [p1, p2, p3]
    settings = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(provider="elevenlabs", elevenlabs=ElevenLabsConfig(api_key="k", voice_id="v")),
    )
    svc = VoiceoverService(tts_client=mock_tts, settings=settings)
    vos = svc.generate_voiceovers_from_snapshot(
        snapshot,
        selected_tracks_by_segment=selected_tracks,
        segment_boundaries=boundaries,
    )

    # 时间排序后应为：seg1 intro(0) -> seg1 after_t2(220) -> seg2 intro(360)
    assert [v.segment_id for v in vos] == ["seg_01", "seg_01_after_1", "seg_02"]
    assert [v.insert_time_in_episode for v in vos] == [0.0, 220.0, 360.0]

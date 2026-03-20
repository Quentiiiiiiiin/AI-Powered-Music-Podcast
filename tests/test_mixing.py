"""混音模块单元测试：crossfade 时长、叠加逻辑。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.models import AudioRenderConfig, SelectedTrack, Track, TrackMetadata, TrackWithMetadata, VoiceoverSegment
from podcast_ai.infra.audio_backend import crossfade_concat, is_ffmpeg_available
from podcast_ai.modules.mixing.mixer import Mixer

_ffmpeg_required = pytest.mark.skipif(not is_ffmpeg_available(), reason="FFmpeg required")


def _make_silent_wav(path: Path, duration_ms: int) -> None:
    """生成短静音 wav 供测试。"""
    seg = AudioSegment.silent(duration=duration_ms)
    seg.export(str(path), format="wav")


def test_crossfade_concat_duration() -> None:
    """crossfade 拼接后总时长 = sum(durations) - (n-1)*crossfade。"""
    segs = [
        AudioSegment.silent(duration=5000),
        AudioSegment.silent(duration=4000),
        AudioSegment.silent(duration=3000),
    ]
    cf_ms = 2000
    out = crossfade_concat(segs, crossfade_seconds=cf_ms / 1000.0)
    expected_ms = 5000 + 4000 + 3000 - 2 * cf_ms
    assert abs(len(out) - expected_ms) < 50


@_ffmpeg_required
def test_mixer_build_mix_single_track(tmp_path: Path) -> None:
    """单曲混音：无 crossfade 重叠。"""
    wav1 = tmp_path / "a.wav"
    _make_silent_wav(wav1, 2000)
    twm = TrackWithMetadata(
        track=Track(id="t1", file_path=wav1, title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=2.0, bpm=100.0, genre=None),
    )
    st = SelectedTrack(track=twm.track, start_time_in_episode=0, end_time_in_episode=2.0, effective_duration=2.0)
    config = AudioRenderConfig(crossfade_seconds=0.5)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st], [], config, out)
    assert out.exists()
    assert summary.track_count == 1
    assert summary.voiceover_count == 0
    assert summary.actual_duration_seconds >= 1.5


@_ffmpeg_required
def test_mixer_build_mix_two_tracks_crossfade(tmp_path: Path) -> None:
    """两曲 crossfade 拼接：总时长 = d1 + d2 - cf。"""
    wav1 = tmp_path / "a.wav"
    wav2 = tmp_path / "b.wav"
    _make_silent_wav(wav1, 3000)
    _make_silent_wav(wav2, 2500)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=wav1, title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=3.0, bpm=100.0, genre=None),
    )
    twm2 = TrackWithMetadata(
        track=Track(id="t2", file_path=wav2, title="B", artist="Y"),
        metadata=TrackMetadata(track_id="t2", duration_seconds=2.5, bpm=105.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0, end_time_in_episode=3.0, effective_duration=3.0)
    st2 = SelectedTrack(track=twm2.track, start_time_in_episode=2.5, end_time_in_episode=5.0, effective_duration=2.0)
    config = AudioRenderConfig(crossfade_seconds=0.5)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], [], config, out)
    assert out.exists()
    expected_dur = 3.0 + 2.5 - 0.5
    assert abs(summary.actual_duration_seconds - expected_dur) < 0.2


# ---------- v1.1 验收测试：串词1→组1→串词2→组2、不重叠、组内 crossfade ----------


def _make_voiceover(path: Path, duration_ms: int) -> VoiceoverSegment:
    """生成主持语音文件并返回 VoiceoverSegment（用于测试）。"""
    seg = AudioSegment.silent(duration=duration_ms)
    seg.export(str(path), format="wav")
    return VoiceoverSegment(
        segment_id=path.stem,
        text="",
        audio_path=path,
        insert_time_in_episode=0.0,
    )


@_ffmpeg_required
def test_v11_episode_starts_with_voiceover(tmp_path: Path) -> None:
    """v1.1：整期开头为串词1，顺序为 串词1→组1→串词2→组2。"""
    vo1 = tmp_path / "vo1.wav"
    vo2 = tmp_path / "vo2.wav"
    _make_silent_wav(tmp_path / "t1.wav", 2000)
    _make_silent_wav(tmp_path / "t2.wav", 1500)
    voiceovers = [
        _make_voiceover(vo1, 500),
        _make_voiceover(vo2, 300),
    ]
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=2.0, bpm=100.0, genre=None),
    )
    twm2 = TrackWithMetadata(
        track=Track(id="t2", file_path=tmp_path / "t2.wav", title="B", artist="Y"),
        metadata=TrackMetadata(track_id="t2", duration_seconds=1.5, bpm=105.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0, end_time_in_episode=2.0, effective_duration=2.0)
    st2 = SelectedTrack(track=twm2.track, start_time_in_episode=2.0, end_time_in_episode=3.5, effective_duration=1.5)
    config = AudioRenderConfig(crossfade_seconds=0.0)  # 组内各 1 首，无 crossfade
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], voiceovers, config, out)
    # 串词1(0.5s) + 组1(2s) + 串词2(0.3s) + 组2(1.5s) = 4.3s，段间无 crossfade
    expected = 0.5 + 2.0 + 0.3 + 1.5
    assert abs(summary.actual_duration_seconds - expected) < 0.05
    assert summary.voiceover_count == 2


@_ffmpeg_required
def test_v11_voiceover_and_music_no_overlap(tmp_path: Path) -> None:
    """v1.1：串词与歌曲不重叠，总时长 = 各部分之和（段间无 crossfade）。"""
    voiceovers = [
        _make_voiceover(tmp_path / "vo1.wav", 400),
        _make_voiceover(tmp_path / "vo2.wav", 200),
    ]
    _make_silent_wav(tmp_path / "t1.wav", 2500)
    _make_silent_wav(tmp_path / "t2.wav", 1800)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=2.5, bpm=100.0, genre=None),
    )
    twm2 = TrackWithMetadata(
        track=Track(id="t2", file_path=tmp_path / "t2.wav", title="B", artist="Y"),
        metadata=TrackMetadata(track_id="t2", duration_seconds=1.8, bpm=105.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0, end_time_in_episode=2.5, effective_duration=2.5)
    st2 = SelectedTrack(track=twm2.track, start_time_in_episode=2.5, end_time_in_episode=4.3, effective_duration=1.8)
    config = AudioRenderConfig(crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], voiceovers, config, out)
    expected = 0.4 + 2.5 + 0.2 + 1.8  # 串词1 + 组1 + 串词2 + 组2
    assert abs(summary.actual_duration_seconds - expected) < 0.05


@_ffmpeg_required
def test_v11_crossfade_within_groups(tmp_path: Path) -> None:
    """v1.1：相邻 segment 之间的歌曲-歌曲转场仍为 crossfade（组内 crossfade）。"""
    voiceovers = [
        _make_voiceover(tmp_path / "vo1.wav", 500),
        _make_voiceover(tmp_path / "vo2.wav", 300),
    ]
    cf = 0.5
    _make_silent_wav(tmp_path / "t1.wav", 3000)
    _make_silent_wav(tmp_path / "t2.wav", 2500)
    _make_silent_wav(tmp_path / "t3.wav", 2000)
    _make_silent_wav(tmp_path / "t4.wav", 2000)
    tracks_meta = [
        TrackWithMetadata(
            track=Track(id=f"t{i}", file_path=tmp_path / f"t{i}.wav", title=f"T{i}", artist="X"),
            metadata=TrackMetadata(track_id=f"t{i}", duration_seconds=3.0 if i <= 2 else 2.0, bpm=100.0, genre=None),
        )
        for i in range(1, 5)
    ]
    selected = []
    t_acc = 0.0
    for i, twm in enumerate(tracks_meta):
        dur = twm.metadata.duration_seconds
        start = t_acc
        end = start + dur
        effective = dur - cf if i > 0 else dur
        t_acc = end - cf
        selected.append(
            SelectedTrack(track=twm.track, start_time_in_episode=start, end_time_in_episode=end, effective_duration=effective)
        )
    config = AudioRenderConfig(crossfade_seconds=cf)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix(selected, voiceovers, config, out)
    # 串词1(0.5) + 组1(3+2.5-cf=5) + 串词2(0.3) + 组2(2+2-cf=3.5) = 9.3
    group1_dur = 3.0 + 2.5 - cf
    group2_dur = 2.0 + 2.0 - cf
    expected = 0.5 + group1_dur + 0.3 + group2_dur
    assert abs(summary.actual_duration_seconds - expected) < 0.1


@_ffmpeg_required
def test_v11_duration_within_tolerance(tmp_path: Path) -> None:
    """v1.1：总时长在既有容差内（±5%）。"""
    voiceovers = [
        _make_voiceover(tmp_path / "vo1.wav", 600),
        _make_voiceover(tmp_path / "vo2.wav", 400),
    ]
    _make_silent_wav(tmp_path / "t1.wav", 5000)
    _make_silent_wav(tmp_path / "t2.wav", 4000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=5.0, bpm=100.0, genre=None),
    )
    twm2 = TrackWithMetadata(
        track=Track(id="t2", file_path=tmp_path / "t2.wav", title="B", artist="Y"),
        metadata=TrackMetadata(track_id="t2", duration_seconds=4.0, bpm=105.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0, end_time_in_episode=5.0, effective_duration=5.0)
    st2 = SelectedTrack(track=twm2.track, start_time_in_episode=5.0, end_time_in_episode=9.0, effective_duration=4.0)
    config = AudioRenderConfig(crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], voiceovers, config, out)
    expected = 0.6 + 5.0 + 0.4 + 4.0  # 10.0s
    tolerance = 0.05 * expected
    assert abs(summary.actual_duration_seconds - expected) < tolerance

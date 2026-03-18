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

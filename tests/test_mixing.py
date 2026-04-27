"""混音模块单元测试：crossfade 时长、叠加逻辑。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydub import AudioSegment  # type: ignore[import-untyped]
from pydub.generators import Sine  # type: ignore[import-untyped]

from podcast_ai.core.models import (
    AudioRenderConfig,
    SelectedTrack,
    Track,
    TrackMetadata,
    TrackWithMetadata,
    VoiceoverSegment,
)
from podcast_ai.infra.audio_backend import crossfade_concat, is_ffmpeg_available, load_audio, simple_normalize
from podcast_ai.modules.mixing.mixer import Mixer

_ffmpeg_required = pytest.mark.skipif(not is_ffmpeg_available(), reason="FFmpeg required")


def _make_silent_wav(path: Path, duration_ms: int) -> None:
    """生成短静音 wav 供测试。"""
    seg = AudioSegment.silent(duration=duration_ms)
    seg.export(str(path), format="wav")


def _export_sine_wav(path: Path, hz: float, duration_ms: int, sample_rate: int = 44_100) -> None:
    """导出纯正弦 wav，供边界/能量断言（与 pydub.load 一致）。"""
    seg = Sine(hz).to_audio_segment(duration=duration_ms)
    seg = seg.set_frame_rate(sample_rate).set_sample_width(2).set_channels(1)
    seg.export(str(path), format="wav")


def _rms_mono(seg: AudioSegment) -> float:
    """单声道 RMS（0～32767 量级即片幅）。"""
    mono = seg.set_channels(1)
    samples = mono.get_array_of_samples()
    if not samples:
        return 0.0
    acc = sum(float(x) * float(x) for x in samples)
    return (acc / len(samples)) ** 0.5


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


def _make_voiceover(
    path: Path, duration_ms: int, insert_time_in_episode: float = 0.0
) -> VoiceoverSegment:
    """生成主持语音文件并返回 VoiceoverSegment（用于测试）。"""
    seg = AudioSegment.silent(duration=duration_ms)
    seg.export(str(path), format="wav")
    return VoiceoverSegment(
        segment_id=path.stem,
        text="",
        audio_path=path,
        insert_time_in_episode=insert_time_in_episode,
    )


def _make_voiceover_mp3(
    path: Path, duration_ms: int, insert_time_in_episode: float = 0.0
) -> VoiceoverSegment:
    """生成 mp3 主持段（模拟 ElevenLabs 默认输出），供 v1.4 混音链路回归。"""
    seg = AudioSegment.silent(duration=duration_ms)
    seg.export(str(path), format="mp3")
    return VoiceoverSegment(
        segment_id=path.stem,
        text="",
        audio_path=path,
        insert_time_in_episode=insert_time_in_episode,
    )


@_ffmpeg_required
def test_v11_episode_starts_with_voiceover(tmp_path: Path) -> None:
    """v1.1：整期开头为串词1，顺序为 串词1→组1→串词2→组2。"""
    vo1 = tmp_path / "vo1.wav"
    vo2 = tmp_path / "vo2.wav"
    _make_silent_wav(tmp_path / "t1.wav", 2000)
    _make_silent_wav(tmp_path / "t2.wav", 1500)
    voiceovers = [
        _make_voiceover(vo1, 500, 0.0),
        _make_voiceover(vo2, 300, 2.0),
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
    # v2.0：段间 crossfade 关闭以锁定 v1.1 无重叠总时长
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
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
        _make_voiceover(tmp_path / "vo1.wav", 400, 0.0),
        _make_voiceover(tmp_path / "vo2.wav", 200, 2.5),
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
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], voiceovers, config, out)
    expected = 0.4 + 2.5 + 0.2 + 1.8  # 串词1 + 组1 + 串词2 + 组2
    assert abs(summary.actual_duration_seconds - expected) < 0.05


@_ffmpeg_required
def test_v11_crossfade_within_groups(tmp_path: Path) -> None:
    """v1.1：相邻 segment 之间的歌曲-歌曲转场仍为 crossfade（组内 crossfade）。"""
    voiceovers = [
        _make_voiceover(tmp_path / "vo1.wav", 500, 0.0),
        _make_voiceover(tmp_path / "vo2.wav", 300, 5.0),
    ]
    cf = 0.5
    _make_silent_wav(tmp_path / "t1.wav", 3000)
    _make_silent_wav(tmp_path / "t2.wav", 2500)
    _make_silent_wav(tmp_path / "t3.wav", 2000)
    _make_silent_wav(tmp_path / "t4.wav", 2000)
    # 元数据时长须与 wav 一致；组 1 为 t1(3s)+t2(2.5s)，组 2 为 t3+t4(各 2s)
    dur_by_i = {1: 3.0, 2: 2.5, 3: 2.0, 4: 2.0}
    tracks_meta = [
        TrackWithMetadata(
            track=Track(id=f"t{i}", file_path=tmp_path / f"t{i}.wav", title=f"T{i}", artist="X"),
            metadata=TrackMetadata(
                track_id=f"t{i}", duration_seconds=dur_by_i[i], bpm=100.0, genre=None
            ),
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
    config = AudioRenderConfig(crossfade_seconds=cf, voice_music_crossfade_seconds=0.0)
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
        _make_voiceover(tmp_path / "vo1.wav", 600, 0.0),
        _make_voiceover(tmp_path / "vo2.wav", 400, 5.0),
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
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], voiceovers, config, out)
    expected = 0.6 + 5.0 + 0.4 + 4.0  # 10.0s
    tolerance = 0.05 * expected
    assert abs(summary.actual_duration_seconds - expected) < tolerance


# ---------- v2.1：串词清晰度优先的边界（音乐→串词硬切；串词→音乐仅音乐淡入） ----------


def _v21_sine_episode_tone_fixture(
    tmp_path: Path,
    vm_seconds: float,
) -> dict:
    """
    构造「极短开场串词 + music1 + 主串词 + music2」时间线，供边界 RMS 断言复用。
    返回毫秒与路径等常量，避免多用例重复贴文件生成代码。
    """
    vm_ms = int(vm_seconds * 1000)
    l1_ms = 200
    g1_ms = 4_000
    v2_ms = 5_000
    g2_ms = 4_000

    vo1_path = tmp_path / "vo_open.wav"
    _make_silent_wav(vo1_path, l1_ms)
    music1_path = tmp_path / "m1.wav"
    _export_sine_wav(music1_path, 220.0, g1_ms)
    vo_main_path = tmp_path / "vo_main.wav"
    _export_sine_wav(vo_main_path, 880.0, v2_ms)
    music2_path = tmp_path / "m2.wav"
    _export_sine_wav(music2_path, 330.0, g2_ms)

    g1_sec = g1_ms / 1000.0
    g2_sec = g2_ms / 1000.0
    voiceovers = [
        VoiceoverSegment(segment_id="open", text="", audio_path=vo1_path, insert_time_in_episode=0.0),
        VoiceoverSegment(segment_id="main", text="", audio_path=vo_main_path, insert_time_in_episode=g1_sec),
    ]
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=music1_path, title="M1", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=g1_sec, bpm=100.0, genre=None),
    )
    twm2 = TrackWithMetadata(
        track=Track(id="t2", file_path=music2_path, title="M2", artist="Y"),
        metadata=TrackMetadata(track_id="t2", duration_seconds=g2_sec, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(
        track=twm1.track,
        start_time_in_episode=0.0,
        end_time_in_episode=g1_sec,
        effective_duration=g1_sec,
    )
    st2 = SelectedTrack(
        track=twm2.track,
        start_time_in_episode=g1_sec,
        end_time_in_episode=g1_sec + g2_sec,
        effective_duration=g2_sec,
    )
    return {
        "vm_seconds": vm_seconds,
        "vm_ms": vm_ms,
        "l1_ms": l1_ms,
        "g1_ms": g1_ms,
        "v2_ms": v2_ms,
        "g2_ms": g2_ms,
        "voiceovers": voiceovers,
        "st1": st1,
        "st2": st2,
        "vo_main_path": vo_main_path,
    }


@_ffmpeg_required
def test_v20_voice_music_crossfade_shortens_mix(tmp_path: Path) -> None:
    """v2.1：单串词+单组仅在串词→音乐窗重叠 vm 秒，总时长仍缩短 vm（与 v2.0 公式一致）。"""
    vm = 1.0
    voiceovers = [_make_voiceover(tmp_path / "vo1.wav", 3000)]
    _make_silent_wav(tmp_path / "t1.wav", 4000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=4.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(
        track=twm1.track, start_time_in_episode=0, end_time_in_episode=4.0, effective_duration=4.0
    )
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=vm)
    mixer = Mixer()
    out = tmp_path / "mix_v20.wav"
    summary = mixer.build_mix([st1], voiceovers, config, out)
    expected = 3.0 + 4.0 - vm
    assert abs(summary.actual_duration_seconds - expected) < 0.2


@_ffmpeg_required
def test_v21_voice_music_clarity_mid_start_tail(tmp_path: Path) -> None:
    """
    v2.1 验收：正弦「串词1→组1→主串词→组2」。
    - 中段：与归一化纯主串词轨 RMS 接近（无人声淡出、中段无背景音乐）。
    - 主串词起点（紧随组1）：音乐→串词硬切，起窗应与纯语音接近（开头不被音乐叠化拉低可懂度）。
    - 主串词尾部 vm 窗：仅音乐淡入与串词尾重叠，混音与纯语音同窗应有明显能量差。
    """
    vm = 1.0
    fx = _v21_sine_episode_tone_fixture(tmp_path, vm)
    vm_ms = fx["vm_ms"]
    l1_ms = fx["l1_ms"]
    g1_ms = fx["g1_ms"]
    v2_ms = fx["v2_ms"]
    vo_main_path = fx["vo_main_path"]

    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=vm)
    mixer = Mixer()
    out = tmp_path / "mix_v21_tones.wav"
    mixer.build_mix([fx["st1"], fx["st2"]], fx["voiceovers"], config, out)

    mix = load_audio(out)
    # 主串词起点：接在组1 硬切之后；组1 前与开场串词叠化，vm 可能被短串词截断
    vm_cfg_ms = int(round(vm * 1000))
    vm_open_music_ms = min(vm_cfg_ms, l1_ms, g1_ms)
    vo2_start_ms = l1_ms + g1_ms - vm_open_music_ms

    # 主串词中段：距首尾均远离 vm 窗
    mid_off = vm_ms + 400
    mid_end_off = v2_ms - vm_ms - 400
    assert mid_end_off > mid_off + 500
    mix_mid = mix[vo2_start_ms + mid_off : vo2_start_ms + mid_end_off]
    ref_vo = simple_normalize(load_audio(vo_main_path), target_dbfs=-16.0)
    ref_mid = ref_vo[mid_off:mid_end_off]
    r_mix_mid = _rms_mono(mix_mid)
    r_ref_mid = _rms_mono(ref_mid)
    assert r_ref_mid > 1.0
    assert abs(r_mix_mid - r_ref_mid) / r_ref_mid < 0.12

    # 主串词起点：硬切后应与纯语音同窗接近（相对误差小）
    edge_ms = 700
    mix_edge = mix[vo2_start_ms : vo2_start_ms + edge_ms]
    ref_edge = ref_vo[:edge_ms]
    r_mix_e = _rms_mono(mix_edge)
    r_ref_e = _rms_mono(ref_edge)
    assert r_ref_e > 0
    rel_start = abs(r_mix_e - r_ref_e) / max(r_mix_e, r_ref_e)
    assert rel_start < 0.15

    # 叠化窗末段：fade_in 前半几乎无声，对比整窗易与纯语音过近；末段音乐已抬起，RMS 差更明显
    tail_focus_ms = min(450, max(200, vm_ms - 200))
    tail_slice = mix[vo2_start_ms + v2_ms - tail_focus_ms : vo2_start_ms + v2_ms]
    ref_tail = ref_vo[v2_ms - tail_focus_ms : v2_ms]
    assert len(tail_slice) >= tail_focus_ms - 2 and len(ref_tail) >= tail_focus_ms - 2
    r_mix_t = _rms_mono(tail_slice)
    r_ref_t = _rms_mono(ref_tail)
    assert r_ref_t > 1.0
    rel_tail = abs(r_mix_t - r_ref_t) / r_ref_t
    assert rel_tail > 0.10


# ---------- v1.4：ElevenLabs 产出 mp3 与混音链路兼容 ----------


@_ffmpeg_required
def test_v14_mixer_accepts_mp3_voiceover_elevenlabs_shape(tmp_path: Path) -> None:
    """v1.4：mp3 串词路径可被 Mixer 加载，与 v1.1 wav 时序逻辑等价（防供应商切换回归）。"""
    vo1 = tmp_path / "vo1.mp3"
    vo2 = tmp_path / "vo2.mp3"
    _make_silent_wav(tmp_path / "t1.wav", 2000)
    _make_silent_wav(tmp_path / "t2.wav", 1500)
    voiceovers = [
        _make_voiceover_mp3(vo1, 500, 0.0),
        _make_voiceover_mp3(vo2, 300, 2.0),
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
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix.wav"
    summary = mixer.build_mix([st1, st2], voiceovers, config, out)
    expected = 0.5 + 2.0 + 0.3 + 1.5
    assert abs(summary.actual_duration_seconds - expected) < 0.08
    assert summary.voiceover_count == 2


@_ffmpeg_required
def test_v39_mixer_supports_multi_voiceovers_by_insert_time(tmp_path: Path) -> None:
    _make_silent_wav(tmp_path / "t1.wav", 2000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=2.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0.0, end_time_in_episode=2.0, effective_duration=2.0)
    vo1 = _make_voiceover(tmp_path / "vo1.wav", 400, 0.0)
    vo2 = _make_voiceover(tmp_path / "vo2.wav", 300, 2.0)
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix_v39.wav"
    summary = mixer.build_mix([st1], [vo1, vo2], config, out)
    # 总时长：音乐 2.0s + 两段串词（无叠化）
    assert abs(summary.actual_duration_seconds - 2.7) < 0.1


# ---------- v3.9.1：时间线校验与「非整轨先叠化」路径回归 ----------


def test_v391_rejects_duplicate_insert_time(tmp_path: Path) -> None:
    """同一锚点两条串词须拒绝，避免时间线歧义。"""
    _make_silent_wav(tmp_path / "t1.wav", 2000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=2.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(
        track=twm1.track, start_time_in_episode=0.0, end_time_in_episode=2.0, effective_duration=2.0
    )
    _make_silent_wav(tmp_path / "v1.wav", 100)
    _make_silent_wav(tmp_path / "v2.wav", 100)
    vo1 = VoiceoverSegment(
        segment_id="a", text="", audio_path=tmp_path / "v1.wav", insert_time_in_episode=0.0
    )
    vo2 = VoiceoverSegment(
        segment_id="b", text="", audio_path=tmp_path / "v2.wav", insert_time_in_episode=0.0
    )
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    with pytest.raises(ValueError, match="insert_time 冲突"):
        mixer.build_mix([st1], [vo1, vo2], config, tmp_path / "out.wav")


def test_v391_rejects_insert_not_on_timeline_anchor(tmp_path: Path) -> None:
    """insert_time 必须落在 0 或某曲 episode start/end 锚点上。"""
    _make_silent_wav(tmp_path / "t1.wav", 2000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=2.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(
        track=twm1.track, start_time_in_episode=0.0, end_time_in_episode=2.0, effective_duration=2.0
    )
    _make_silent_wav(tmp_path / "v1.wav", 100)
    vo1 = VoiceoverSegment(
        segment_id="a", text="", audio_path=tmp_path / "v1.wav", insert_time_in_episode=1.0
    )
    config = AudioRenderConfig(crossfade_seconds=0.0, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    with pytest.raises(ValueError, match="非合法锚点"):
        mixer.build_mix([st1], [vo1], config, tmp_path / "out.wav")


@_ffmpeg_required
def test_v391_no_single_crossfade_of_entire_tracklist(monkeypatch, tmp_path: Path) -> None:
    """
    v3.9.1：有串词时按块组内叠化，不得出现「先对全部 selected_tracks 做一次 crossfade_concat」
    的旧实现（可通过单次调用片段数观测）。
    """
    from podcast_ai.modules.mixing import mixer as mixer_mod

    chunk_lengths: list[int] = []

    def _spy(segments, crossfade_seconds):
        segs = list(segments)
        chunk_lengths.append(len(segs))
        return crossfade_concat(segs, crossfade_seconds)

    monkeypatch.setattr(mixer_mod, "crossfade_concat", _spy)

    voiceovers = [
        _make_voiceover(tmp_path / "vo1.wav", 500, 0.0),
        _make_voiceover(tmp_path / "vo2.wav", 300, 5.0),
    ]
    cf = 0.5
    _make_silent_wav(tmp_path / "t1.wav", 3000)
    _make_silent_wav(tmp_path / "t2.wav", 2500)
    for i in (3, 4):
        _make_silent_wav(tmp_path / f"t{i}.wav", 2000)
    dur_by_i = {1: 3.0, 2: 2.5, 3: 2.0, 4: 2.0}
    tracks_meta = [
        TrackWithMetadata(
            track=Track(id=f"t{i}", file_path=tmp_path / f"t{i}.wav", title=f"T{i}", artist="X"),
            metadata=TrackMetadata(
                track_id=f"t{i}", duration_seconds=dur_by_i[i], bpm=100.0, genre=None
            ),
        )
        for i in range(1, 5)
    ]
    selected: list[SelectedTrack] = []
    t_acc = 0.0
    for i, twm in enumerate(tracks_meta):
        dur = twm.metadata.duration_seconds
        start = t_acc
        end = start + dur
        effective = dur - cf if i > 0 else dur
        t_acc = end - cf
        selected.append(
            SelectedTrack(
                track=twm.track,
                start_time_in_episode=start,
                end_time_in_episode=end,
                effective_duration=effective,
            )
        )
    config = AudioRenderConfig(crossfade_seconds=cf, voice_music_crossfade_seconds=0.0)
    mixer = Mixer()
    out = tmp_path / "mix_v391_spy.wav"
    mixer.build_mix(selected, voiceovers, config, out)
    assert 4 not in chunk_lengths, "不应一次性叠化全部 4 首曲目"
    assert 2 in chunk_lengths


# ---------- v4.2：voice->music intro 对齐 ----------


@_ffmpeg_required
def test_v42_voice_to_music_uses_intro_estimate_when_available(monkeypatch, tmp_path: Path) -> None:
    """估计成功且高于默认 vm 时，应放大 vm。"""
    from podcast_ai.modules.mixing import mixer as mixer_mod

    _make_silent_wav(tmp_path / "t1.wav", 4000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=4.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0.0, end_time_in_episode=4.0, effective_duration=4.0)
    voiceovers = [_make_voiceover(tmp_path / "vo1.wav", 3000, 0.0)]

    class _Est:
        intro_seconds = 1.2
        confidence = 0.9
        reason = "ok"

    monkeypatch.setattr(mixer_mod, "estimate_track_intro_seconds", lambda *args, **kwargs: _Est())
    config = AudioRenderConfig(
        crossfade_seconds=0.0,
        voice_music_crossfade_seconds=0.5,
        voice_music_intro_align_enabled=True,
        voice_music_intro_align_max_seconds=2.0,
    )
    mixer = Mixer()
    out = tmp_path / "mix_v42_intro.wav"
    summary = mixer.build_mix([st1], voiceovers, config, out)
    # vm=max(0.5, min(1.2,2.0))=1.2 => 3.0 + 4.0 - 1.2 = 5.8
    assert abs(summary.actual_duration_seconds - 5.8) < 0.2


@_ffmpeg_required
def test_v42_voice_to_music_uses_base_vm_as_lower_bound(monkeypatch, tmp_path: Path) -> None:
    """方案1：当 intro 估计小于默认 vm 时，仍使用默认 vm 作为下限。"""
    from podcast_ai.modules.mixing import mixer as mixer_mod

    _make_silent_wav(tmp_path / "t1.wav", 4000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=4.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0.0, end_time_in_episode=4.0, effective_duration=4.0)
    voiceovers = [_make_voiceover(tmp_path / "vo1.wav", 3000, 0.0)]

    class _Est:
        intro_seconds = 0.7
        confidence = 0.8
        reason = "ok"

    monkeypatch.setattr(mixer_mod, "estimate_track_intro_seconds", lambda *args, **kwargs: _Est())
    config = AudioRenderConfig(
        crossfade_seconds=0.0,
        voice_music_crossfade_seconds=3.0,
        voice_music_intro_align_enabled=True,
        voice_music_intro_align_max_seconds=8.0,
    )
    mixer = Mixer()
    out = tmp_path / "mix_v42_base_lb.wav"
    summary = mixer.build_mix([st1], voiceovers, config, out)
    # vm=max(3.0, min(0.7,8.0))=3.0 => 3.0 + 4.0 - 3.0 = 4.0
    assert abs(summary.actual_duration_seconds - 4.0) < 0.2


@_ffmpeg_required
def test_v42_voice_to_music_fallback_to_default_vm_on_intro_fail(monkeypatch, caplog, tmp_path: Path) -> None:
    """估计失败时回退默认 vm，并打 warning。"""
    from podcast_ai.modules.mixing import mixer as mixer_mod

    _make_silent_wav(tmp_path / "t1.wav", 4000)
    twm1 = TrackWithMetadata(
        track=Track(id="t1", file_path=tmp_path / "t1.wav", title="A", artist="X"),
        metadata=TrackMetadata(track_id="t1", duration_seconds=4.0, bpm=100.0, genre=None),
    )
    st1 = SelectedTrack(track=twm1.track, start_time_in_episode=0.0, end_time_in_episode=4.0, effective_duration=4.0)
    voiceovers = [_make_voiceover(tmp_path / "vo1.wav", 3000, 0.0)]

    class _Est:
        intro_seconds = None
        confidence = 0.0
        reason = "librosa_unavailable"

    monkeypatch.setattr(mixer_mod, "estimate_track_intro_seconds", lambda *args, **kwargs: _Est())
    caplog.set_level("WARNING")
    config = AudioRenderConfig(
        crossfade_seconds=0.0,
        voice_music_crossfade_seconds=1.0,
        voice_music_intro_align_enabled=True,
        voice_music_intro_align_max_seconds=1.0,
    )
    mixer = Mixer()
    out = tmp_path / "mix_v42_fallback.wav"
    summary = mixer.build_mix([st1], voiceovers, config, out)
    # 回退默认 vm=1.0：3.0 + 4.0 - 1.0 = 6.0
    assert abs(summary.actual_duration_seconds - 6.0) < 0.2
    assert "intro 估计失败" in caplog.text

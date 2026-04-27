from __future__ import annotations

from pathlib import Path

import numpy as np

from podcast_ai.modules.mixing.intro_align import estimate_track_intro_seconds


def test_intro_align_returns_fallback_when_track_missing(tmp_path: Path) -> None:
    out = estimate_track_intro_seconds(tmp_path / "no_such_file.wav", max_intro_seconds=3.0)
    assert out.intro_seconds is None
    assert out.reason == "track_not_found"


class _FakeOnset:
    def __init__(self, onset_env: np.ndarray, onset_frames: np.ndarray) -> None:
        self._onset_env = onset_env
        self._onset_frames = onset_frames

    def onset_strength(self, y, sr, hop_length):  # noqa: ANN001
        return self._onset_env

    def onset_detect(self, onset_envelope, sr, hop_length, units, backtrack):  # noqa: ANN001
        return self._onset_frames


class _FakeLibrosa:
    def __init__(self, rms: np.ndarray, onset_env: np.ndarray, onset_frames: np.ndarray, sr: int = 1000, hop: int = 100):
        self._rms = rms
        self._sr = sr
        self._hop = hop
        self.feature = type("_Feature", (), {"rms": lambda _self, y, hop_length: np.asarray([self._rms])})()
        self.onset = _FakeOnset(onset_env=onset_env, onset_frames=onset_frames)

    def load(self, path, sr=None, mono=True):  # noqa: ANN001
        # 仅保证长度足够通过 audio_too_short 判定
        return np.zeros(int(self._sr * 1.2), dtype=float), self._sr

    def frames_to_time(self, frames: int, sr: int, hop_length: int) -> float:
        return float(frames * hop_length / sr)


def test_intro_align_r1_ignores_single_frame_noise(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.wav"
    p.write_bytes(b"x")
    rms = np.asarray([0.01, 0.9, 0.02, 0.02, 0.03, 0.5, 0.52, 0.53, 0.55, 0.56], dtype=float)
    fake = _FakeLibrosa(rms=rms, onset_env=np.zeros_like(rms), onset_frames=np.asarray([], dtype=int))
    monkeypatch.setattr("podcast_ai.modules.mixing.intro_align._lazy_import_librosa", lambda: fake)
    out = estimate_track_intro_seconds(p, max_intro_seconds=3.0)
    assert out.intro_seconds is not None
    # 稳定段应落在后半，不应被单帧噪声(0.1s)提前触发
    assert out.intro_seconds >= 0.45


def test_intro_align_r2_filters_ornamental_onset(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "b.wav"
    p.write_bytes(b"x")
    rms = np.asarray([0.02, 0.02, 0.03, 0.03, 0.04, 0.20, 0.22, 0.24, 0.26, 0.28], dtype=float)
    onset_env = np.asarray([0.1, 0.9, 0.15, 0.1, 0.2, 0.3, 0.4, 1.0, 0.5, 0.4], dtype=float)
    onset_frames = np.asarray([1, 7], dtype=int)
    fake = _FakeLibrosa(rms=rms, onset_env=onset_env, onset_frames=onset_frames)
    monkeypatch.setattr("podcast_ai.modules.mixing.intro_align._lazy_import_librosa", lambda: fake)
    out = estimate_track_intro_seconds(p, max_intro_seconds=3.0)
    assert out.intro_seconds is not None
    # 首个装饰音在 0.1s，应被过滤，最终不应过早
    assert out.intro_seconds >= 0.5
    assert out.reason in {"ok_onset_supported", "ok_consistent", "low_dynamic_relaxed"}


def test_intro_align_low_dynamic_relaxed_path(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "c.wav"
    p.write_bytes(b"x")
    rms = np.asarray([0.10, 0.11, 0.10, 0.11, 0.12, 0.12, 0.13, 0.13, 0.14, 0.14], dtype=float)
    onset_env = np.asarray([0.2, 0.25, 0.2, 0.21, 0.3, 0.35, 0.4, 0.45, 0.4, 0.35], dtype=float)
    onset_frames = np.asarray([6], dtype=int)
    fake = _FakeLibrosa(rms=rms, onset_env=onset_env, onset_frames=onset_frames)
    monkeypatch.setattr("podcast_ai.modules.mixing.intro_align._lazy_import_librosa", lambda: fake)
    out = estimate_track_intro_seconds(p, max_intro_seconds=3.0)
    assert out.intro_seconds is not None
    assert out.reason == "low_dynamic_relaxed"


def test_intro_align_librosa_unavailable_reason(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "d.wav"
    p.write_bytes(b"x")
    monkeypatch.setattr(
        "podcast_ai.modules.mixing.intro_align._lazy_import_librosa",
        lambda: (_ for _ in ()).throw(ImportError("no librosa")),
    )
    out = estimate_track_intro_seconds(p, max_intro_seconds=3.0)
    assert out.intro_seconds is None
    assert out.reason == "librosa_unavailable"


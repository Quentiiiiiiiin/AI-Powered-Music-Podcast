from __future__ import annotations

from pathlib import Path

import numpy as np

from podcast_ai.modules.mixing.intro_align import estimate_track_intro_seconds


def test_intro_align_returns_fallback_when_track_missing(tmp_path: Path) -> None:
    out = estimate_track_intro_seconds(tmp_path / "no_such_file.wav", max_intro_seconds=3.0)
    assert out.intro_seconds is None
    assert out.reason == "track_not_found"


def test_intro_align_max_intro_non_positive(tmp_path: Path) -> None:
    p = tmp_path / "a.wav"
    p.write_bytes(b"x")
    out = estimate_track_intro_seconds(p, max_intro_seconds=0.0)
    assert out.intro_seconds is None
    assert out.reason == "max_intro_seconds<=0"


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


def _write_mono_wav(path: Path, y: np.ndarray, sr: int = 22050) -> None:
    import wave

    y16 = np.clip(y, -1.0, 1.0)
    y16 = (y16 * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(y16.tobytes())


def test_intro_align_detects_mid_song_structure_lift(tmp_path: Path) -> None:
    """前半稀疏、约 8s 后 mid 能量显著抬升时，估计应靠近抬升点而非贴近 0。"""
    sr = 22050
    duration = 20.0
    t = np.arange(int(sr * duration), dtype=float) / sr
    # 稀疏垫底
    y = 0.05 * np.sin(2 * np.pi * 110.0 * t)
    # 8s 后加入中高频内容（模拟主段/人声进入）
    enter = 8.0
    mask = t >= enter
    y = y + mask * (
        0.35 * np.sin(2 * np.pi * 660.0 * t)
        + 0.25 * np.sin(2 * np.pi * 990.0 * t)
        + 0.12 * np.random.default_rng(0).normal(0.0, 1.0, size=t.size)
    )
    # 轻 attack，避免完全方波
    attack = np.clip((t - enter) / 0.15, 0.0, 1.0)
    y = 0.05 * np.sin(2 * np.pi * 110.0 * t) + attack * (
        0.35 * np.sin(2 * np.pi * 660.0 * t)
        + 0.25 * np.sin(2 * np.pi * 990.0 * t)
        + 0.12 * np.random.default_rng(0).normal(0.0, 1.0, size=t.size)
    )

    p = tmp_path / "structure.wav"
    _write_mono_wav(p, y, sr=sr)
    out = estimate_track_intro_seconds(p, max_intro_seconds=18.0)
    assert out.intro_seconds is not None
    assert 5.0 <= out.intro_seconds <= 12.0
    assert out.reason.startswith("ok_") or out.reason.startswith("low_dynamic")


def test_intro_align_respects_max_intro_cap(tmp_path: Path) -> None:
    sr = 22050
    duration = 12.0
    t = np.arange(int(sr * duration), dtype=float) / sr
    y = 0.2 * np.sin(2 * np.pi * 440.0 * t)
    p = tmp_path / "cap.wav"
    _write_mono_wav(p, y, sr=sr)
    out = estimate_track_intro_seconds(p, max_intro_seconds=2.5)
    assert out.intro_seconds is not None
    assert out.intro_seconds <= 2.5 + 1e-6

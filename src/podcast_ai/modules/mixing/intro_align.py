from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class IntroEstimate:
    """下一首曲目 intro 估计结果。"""

    intro_seconds: float | None
    confidence: float
    reason: str


def _lazy_import_librosa():
    import librosa  # type: ignore[import-untyped]

    return librosa


def estimate_track_intro_seconds(
    track_path: Path,
    *,
    max_intro_seconds: float,
) -> IntroEstimate:
    """
    用轻量启发式估计曲目 intro 结束时刻（秒）。

    规则（v4.2，保持简单可维护）：
    - RMS 包络抬升点：找能量超过高分位阈值的最早点。
    - Onset 点：找首个较稳定起音点。
    - 候选取 max(rms_lift, first_onset)，使叠入更接近 intro 将尽、主歌将起。
    - 若 librosa 不可用、音频过短/近静音、候选无效，则返回 None 交由调用方回退默认 vm。
    """
    if max_intro_seconds <= 0:
        return IntroEstimate(intro_seconds=None, confidence=0.0, reason="max_intro_seconds<=0")
    if not track_path.exists():
        return IntroEstimate(intro_seconds=None, confidence=0.0, reason="track_not_found")

    try:
        librosa = _lazy_import_librosa()
    except Exception:
        return IntroEstimate(intro_seconds=None, confidence=0.0, reason="librosa_unavailable")

    try:
        y, sr = librosa.load(str(track_path), sr=None, mono=True)
    except Exception:
        return IntroEstimate(intro_seconds=None, confidence=0.0, reason="load_failed")

    if y.size < int(sr * 0.8):
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="audio_too_short")

    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    if rms.size < 8:
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="rms_too_short")

    p90 = float(np.percentile(rms, 90))
    if p90 <= 1e-8:
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="near_silence")
    lift_threshold = max(p90 * 0.35, 1e-5)
    lift_idx = int(np.argmax(rms >= lift_threshold))
    if not bool(np.any(rms >= lift_threshold)):
        return IntroEstimate(intro_seconds=None, confidence=0.2, reason="no_rms_lift")
    rms_lift_t = float(librosa.frames_to_time(lift_idx, sr=sr, hop_length=hop_length))

    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            units="frames",
            backtrack=False,
        )
        onset_t = (
            float(librosa.frames_to_time(int(onset_frames[0]), sr=sr, hop_length=hop_length))
            if len(onset_frames) > 0
            else rms_lift_t
        )
    except Exception:
        onset_t = rms_lift_t

    intro_t = max(rms_lift_t, onset_t)
    intro_t = min(intro_t, float(max_intro_seconds))
    if intro_t <= 0.05:
        return IntroEstimate(intro_seconds=None, confidence=0.2, reason="intro_too_small")

    confidence = 0.75 if abs(rms_lift_t - onset_t) <= 1.0 else 0.55
    return IntroEstimate(intro_seconds=float(intro_t), confidence=confidence, reason="ok")


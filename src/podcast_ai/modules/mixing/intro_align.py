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

    规则（v4.4）：
    - R1（抗噪）：RMS 触发改为“连续 N 帧 + 最短稳定时长”。
    - R2（抗装饰音）：onset 需满足强度分位阈值 + 后续短窗能量支持。
    - 低动态场景启用自适应放宽（降低 onset 分位阈值/支撑阈值）。
    - 对外契约不变：始终返回 IntroEstimate；失败时 intro_seconds=None + 明确 reason。
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
    rms = np.asarray(librosa.feature.rms(y=y, hop_length=hop_length)[0], dtype=float)
    if rms.size < 8:
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="rms_too_short")

    p90 = float(np.percentile(rms, 90))
    if p90 <= 1e-8:
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="near_silence")

    # ---- R1: RMS 稳定抬升 ----
    eps = 1e-9
    p20 = float(np.percentile(rms, 20))
    p95 = float(np.percentile(rms, 95))
    dynamic_range_db = float(20.0 * np.log10((p95 + eps) / (p20 + eps)))
    low_dynamic = dynamic_range_db < 8.0

    stable_frames = 3 if low_dynamic else 4
    min_stable_ms = 120.0
    frame_ms = (hop_length / float(sr)) * 1000.0
    stable_frames_by_ms = max(1, int(np.ceil(min_stable_ms / max(frame_ms, 1e-6))))
    stable_frames = max(stable_frames, stable_frames_by_ms)
    lift_threshold = max(p90 * (0.30 if low_dynamic else 0.35), 1e-5)

    above = rms >= lift_threshold
    run = 0
    lift_idx: int | None = None
    for i, flag in enumerate(above):
        run = run + 1 if bool(flag) else 0
        if run >= stable_frames:
            lift_idx = i - stable_frames + 1
            break
    if lift_idx is None:
        return IntroEstimate(intro_seconds=None, confidence=0.2, reason="rms_no_stable_lift")
    rms_lift_t = float(librosa.frames_to_time(int(lift_idx), sr=sr, hop_length=hop_length))

    # ---- R2: onset 双重过滤（强度 + 能量支撑） ----
    onset_t = rms_lift_t
    onset_supported = False
    onset_reason = "onset_missing"
    try:
        onset_env = np.asarray(librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length), dtype=float)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            units="frames",
            backtrack=False,
        )
        if len(onset_frames) > 0:
            onset_percentile = 70.0 if low_dynamic else 78.0
            onset_strength_thr = float(np.percentile(onset_env, onset_percentile))
            support_ms = 300.0
            support_frames = max(1, int(np.ceil(support_ms / max(frame_ms, 1e-6))))
            support_ratio = 0.75 if low_dynamic else 0.85
            for f in onset_frames:
                fi = int(f)
                if fi < 0 or fi >= len(onset_env):
                    continue
                if onset_env[fi] < onset_strength_thr:
                    onset_reason = "onset_filtered_out"
                    continue
                end = min(len(rms), fi + support_frames)
                support_mean = float(np.mean(rms[fi:end])) if end > fi else 0.0
                if support_mean >= lift_threshold * support_ratio:
                    onset_t = float(librosa.frames_to_time(fi, sr=sr, hop_length=hop_length))
                    onset_supported = True
                    onset_reason = "onset_supported"
                    break
                onset_reason = "onset_no_energy_support"
        else:
            onset_reason = "onset_missing"
    except Exception:
        onset_reason = "onset_analyze_failed"

    intro_t = max(rms_lift_t, onset_t)
    intro_t = min(intro_t, float(max_intro_seconds))
    if intro_t <= 0.05:
        return IntroEstimate(intro_seconds=None, confidence=0.2, reason="intro_too_small")

    delta = abs(rms_lift_t - onset_t)
    confidence = 0.45
    confidence += 0.2 if onset_supported else 0.05
    confidence += 0.15 if delta <= 0.8 else 0.05
    confidence += 0.05 if low_dynamic else 0.1
    confidence = float(min(0.95, max(0.0, confidence)))

    if low_dynamic:
        reason = "low_dynamic_relaxed"
    elif onset_supported and delta <= 0.8:
        reason = "ok_consistent"
    elif onset_supported:
        reason = "ok_onset_supported"
    else:
        reason = onset_reason if onset_reason.startswith("onset_") else "ok_rms_only"
    return IntroEstimate(intro_seconds=float(intro_t), confidence=confidence, reason=reason)


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


def _nz(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    lo, hi = np.percentile(a, 5), np.percentile(a, 95)
    return np.clip((a - lo) / max(hi - lo, 1e-9), 0.0, 1.0)


def _smooth(a: np.ndarray, win: int = 9) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    if a.size < 3:
        return a
    w = max(3, int(win))
    if w % 2 == 0:
        w += 1
    return np.convolve(a, np.ones(w) / w, mode="same")


def _find_local_maxima(x: np.ndarray, min_dist: int) -> list[int]:
    peaks: list[int] = []
    for i in range(1, len(x) - 1):
        if x[i] >= x[i - 1] and x[i] >= x[i + 1]:
            if not peaks or i - peaks[-1] >= min_dist:
                peaks.append(i)
            elif x[i] > x[peaks[-1]]:
                peaks[-1] = i
    return peaks


def _rolling_mean_before_after(x: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x, dtype=float)
    c = np.concatenate([[0.0], np.cumsum(x)])
    before = np.zeros_like(x)
    after = np.zeros_like(x)
    idx = np.arange(len(x))
    valid_b = idx >= n
    before[valid_b] = (c[idx[valid_b]] - c[idx[valid_b] - n]) / n
    valid_a = idx + n <= len(x)
    after[valid_a] = (c[idx[valid_a] + n] - c[idx[valid_a]]) / n
    return before, after


def _first_energy_onset_seconds(times: np.ndarray, rms: np.ndarray) -> float:
    thr = max(float(np.percentile(rms, 90)) * 0.35, 1e-5)
    stable = 4
    run = 0
    for i, v in enumerate(rms):
        run = run + 1 if float(v) >= thr else 0
        if run >= stable:
            return float(times[i - stable + 1])
    return 0.0


def estimate_track_intro_seconds(
    track_path: Path,
    *,
    max_intro_seconds: float,
) -> IntroEstimate:
    """
    用轻量启发式估计曲目 intro 结束时刻（秒）。

    规则（v4.6）：
    - 旧版 R1/R2 易把“音乐起声”当成“前奏结束”，系统性偏早。
    - 改为在起声之后搜索**结构边界**：多尺度 mid/rms/high 能量跳变 + mel/onset novelty。
    - 在足够强的 novelty 峰中，结合轻度时间先验与“勿过晚”惩罚选峰，并就近吸附到 onset。
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

    # 仅分析前奏窗口；统一重采样以稳定 hop 时间分辨率。
    analyze_seconds = float(max_intro_seconds) + 1.0
    try:
        y, sr = librosa.load(
            str(track_path),
            sr=22050,
            mono=True,
            duration=analyze_seconds,
        )
    except Exception:
        return IntroEstimate(intro_seconds=None, confidence=0.0, reason="load_failed")

    if y.size < int(sr * 0.8):
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="audio_too_short")

    hop_length = 512
    rms_raw = np.asarray(librosa.feature.rms(y=y, hop_length=hop_length)[0], dtype=float)
    if rms_raw.size < 8:
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="rms_too_short")

    p90 = float(np.percentile(rms_raw, 90))
    if p90 <= 1e-8:
        return IntroEstimate(intro_seconds=None, confidence=0.1, reason="near_silence")

    times = np.asarray(
        librosa.frames_to_time(np.arange(rms_raw.size), sr=sr, hop_length=hop_length),
        dtype=float,
    )
    dt = float(times[1] - times[0]) if times.size > 1 else hop_length / float(sr)

    try:
        onset_raw = np.asarray(
            librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length),
            dtype=float,
        )
        # 与 rms 帧数对齐（偶发差 1 帧）
        n = min(rms_raw.size, onset_raw.size)
        rms_raw = rms_raw[:n]
        onset_raw = onset_raw[:n]
        times = times[:n]

        S = np.asarray(
            librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop_length, n_mels=64, fmax=8000),
            dtype=float,
        )
        S = S[:, :n]
        S_db = librosa.power_to_db(S, ref=np.max)
        freqs = librosa.mel_frequencies(n_mels=64, fmax=8000)
        low = S[(freqs < 200)].mean(axis=0) if np.any(freqs < 200) else S[:5].mean(axis=0)
        mid = S[(freqs >= 300) & (freqs <= 3000)].mean(axis=0)
        high = S[(freqs > 3000)].mean(axis=0)
        mel_diff = np.sqrt(np.sum(np.diff(S_db, axis=1) ** 2, axis=0))
        mel_diff = np.concatenate([[0.0], mel_diff])[:n]
        _ = low  # reserved for future band-ratio features
    except Exception:
        return IntroEstimate(intro_seconds=None, confidence=0.2, reason="feature_extract_failed")

    rms = _smooth(rms_raw, 11)
    mid_s = _smooth(np.log1p(mid), 11)
    high_s = _smooth(np.log1p(high), 11)
    onset = _smooth(onset_raw, 9)
    mel = _smooth(mel_diff, 9)

    eps = 1e-9
    p20 = float(np.percentile(rms, 20))
    p95 = float(np.percentile(rms, 95))
    dynamic_range_db = float(20.0 * np.log10((p95 + eps) / (p20 + eps)))
    low_dynamic = dynamic_range_db < 8.0

    t0 = _first_energy_onset_seconds(times, rms)

    # 多尺度能量跳变：捕捉 intro→主段 的结构抬升，而不是首个起声。
    jump_acc = np.zeros(n, dtype=float)
    combo = 0.60 * mid_s + 0.25 * rms + 0.15 * high_s
    for sc in (1.5, 2.5, 4.0):
        win = max(1, int(sc / max(dt, 1e-6)))
        before, after = _rolling_mean_before_after(combo, win)
        jump = np.maximum(0.0, after - before)
        jump[:win] = 0.0
        jump[max(0, n - win) :] = 0.0
        jump_acc += jump
    jump_acc /= 3.0

    novelty = (
        0.40 * _nz(jump_acc)
        + 0.25 * _nz(mel)
        + 0.20 * _nz(onset)
        + 0.15 * _nz(np.maximum(0.0, np.gradient(mid_s)))
    )

    start = max(1.0, t0 + 0.5)
    end = float(max_intro_seconds)
    mask = (times >= start) & (times <= end)
    idx = np.where(mask)[0]
    if idx.size == 0:
        if t0 <= 0.05:
            return IntroEstimate(intro_seconds=None, confidence=0.2, reason="no_search_window")
        intro_t = min(t0, end)
        if intro_t <= 0.05:
            return IntroEstimate(intro_seconds=None, confidence=0.2, reason="intro_too_small")
        return IntroEstimate(
            intro_seconds=float(intro_t),
            confidence=0.35,
            reason="fallback_energy_onset",
        )

    local = novelty[idx]
    min_dist = max(1, int(1.2 / max(dt, 1e-6)))
    peaks = _find_local_maxima(local, min_dist)
    if not peaks:
        best_i = int(idx[int(np.argmax(local))])
        intro_t = float(times[best_i])
        reason = "ok_novelty_argmax"
        peak_strength = float(local.max())
        snapped = False
    else:
        peak_vals = local[np.asarray(peaks, dtype=int)]
        vmax = float(peak_vals.max())
        jump_n = _nz(jump_acc)
        rel = 0.48
        prior_mu = 11.0
        prior_sig = 8.0
        early_pen = 0.015
        best_s = -1e9
        best_t = None
        best_v = 0.0
        for p, v in zip(peaks, peak_vals):
            if float(v) < rel * vmax:
                continue
            gi = int(idx[int(p)])
            t = float(times[gi])
            prior = float(np.exp(-0.5 * ((t - prior_mu) / prior_sig) ** 2))
            score = (0.62 * float(v) + 0.20 * float(jump_n[gi]) + 0.18 * prior) / (
                1.0 + early_pen * max(0.0, t - t0)
            )
            if score > best_s:
                best_s = score
                best_t = t
                best_v = float(v)
        if best_t is None:
            gi = int(idx[int(peaks[int(np.argmax(peak_vals))])])
            best_t = float(times[gi])
            best_v = float(vmax)
        intro_t = best_t
        peak_strength = best_v / max(vmax, 1e-9)
        reason = "low_dynamic_structure" if low_dynamic else "ok_structure_peak"
        snapped = False

        # 就近吸附到 onset 峰，贴合人声/鼓点进入点。
        onset_s = _smooth(onset_raw, 5)
        radius = max(1, int(0.8 / max(dt, 1e-6)))
        i0 = int(np.argmin(np.abs(times - intro_t)))
        lo = max(0, i0 - radius)
        hi = min(n, i0 + radius + 1)
        local_on = onset_s[lo:hi]
        if local_on.size and float(local_on.max()) >= float(np.percentile(onset_s, 60)):
            intro_t = float(times[lo + int(np.argmax(local_on))])
            snapped = True
            if not low_dynamic:
                reason = "ok_structure_onset_snap"

    intro_t = min(max(float(intro_t), 0.0), float(max_intro_seconds))
    if intro_t <= 0.05:
        return IntroEstimate(intro_seconds=None, confidence=0.2, reason="intro_too_small")

    confidence = 0.42
    confidence += 0.18 if peak_strength >= 0.75 else 0.08
    confidence += 0.12 if snapped else 0.05
    confidence += 0.08 if not low_dynamic else 0.04
    # 过早起声回退通常更不可靠
    if reason == "fallback_energy_onset":
        confidence = min(confidence, 0.4)
    confidence = float(min(0.95, max(0.0, confidence)))
    return IntroEstimate(intro_seconds=float(intro_t), confidence=confidence, reason=reason)

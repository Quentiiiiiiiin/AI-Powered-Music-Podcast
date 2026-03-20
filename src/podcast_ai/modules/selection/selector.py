"""
自动选曲与排序：结合 EpisodePlan 的段落目标与 BPM 区间，输出按 BPM 递增、相邻差值受控的 SelectedTrack 列表。

v1.2：新增 select_tracks_by_plan，严格按 plan 顺序映射本地候选，不做 BPM 过滤/排序/贪心。
"""
from __future__ import annotations

import logging
from typing import Optional

from podcast_ai.core.exceptions import PlanMappingError
from podcast_ai.core.models import EpisodePlan, SelectedTrack, TrackWithMetadata

logger = logging.getLogger(__name__)

# 相邻曲目 BPM 最大允许差值（避免听感突兀）
DEFAULT_MAX_BPM_JUMP = 20

# 目标时长容差：±5%
TARGET_DURATION_TOLERANCE = 0.05


def _match_score(recommended: str, twm: TrackWithMetadata) -> float:
    """
    推荐曲目字符串与本地候选的匹配得分（越高越匹配）。
    基于文件名 stem、title、artist 做简单包含/相等判断。
    """
    rec = (recommended or "").strip().lower()
    if not rec:
        return 0.0
    score = 0.0
    stem = (twm.track.file_path.stem or "").lower()
    title = (twm.track.title or "").strip().lower()
    artist = (twm.track.artist or "").strip().lower()
    for field in (stem, title, artist):
        if not field:
            continue
        if rec == field:
            score = max(score, 3.0)
        elif rec in field or field in rec:
            score = max(score, 2.0)
    return score


def _find_best_match(recommended: str, library: list[TrackWithMetadata]) -> TrackWithMetadata | None:
    """在 library 中找与 recommended 最匹配的曲目；无匹配时返回 None。"""
    if not recommended or not library:
        return None
    best: TrackWithMetadata | None = None
    best_score = 0.0
    for twm in library:
        s = _match_score(recommended, twm)
        if s > best_score:
            best_score = s
            best = twm
    return best if best_score >= 1.0 else None


def select_tracks_by_plan(
    plan: EpisodePlan,
    library: list[TrackWithMetadata],
    crossfade_seconds: float,
) -> list[SelectedTrack]:
    """
    v1.2：严格按 plan 顺序映射本地候选，生成 SelectedTrack 列表。
    遍历 plan.segments[].target_playlist[].recommended_tracks，对每个推荐曲目做本地映射；
    无法映射时抛出 PlanMappingError（含 segment/playlist 索引与推荐曲目文本）。
    crossfade_seconds 仅用于时间线 start/end/effective_duration 计算，不参与排序。
    """
    cf = crossfade_seconds
    mapped: list[TrackWithMetadata] = []

    for seg_idx, seg in enumerate(plan.segments):
        for item_idx, item in enumerate(seg.target_playlist):
            for rec_idx, rec_text in enumerate(item.recommended_tracks):
                match = _find_best_match(rec_text, library)
                if match is None:
                    raise PlanMappingError(
                        f"无法映射：segment[{seg_idx}]「{seg.name}」"
                        f" target_playlist[{item_idx}].recommended_tracks[{rec_idx}]"
                        f"「{rec_text}」在本地候选库中无匹配。"
                    )
                mapped.append(match)

    # 按 plan 顺序构建 SelectedTrack，计算时间线
    result: list[SelectedTrack] = []
    t_acc = 0.0
    for i, twm in enumerate(mapped):
        dur = twm.metadata.duration_seconds
        start = t_acc
        end = start + dur
        effective = dur - cf if i > 0 else dur
        t_acc = end - cf
        result.append(
            SelectedTrack(
                track=twm.track,
                start_time_in_episode=start,
                end_time_in_episode=end,
                effective_duration=effective,
            ),
        )

    logger.info("plan 驱动选曲完成: %d 首（严格按 plan 顺序）", len(result))
    return result


class TrackSelector:
    """
    在候选曲目中结合 EpisodePlan 的段落目标时长与 BPM 区间进行过滤/打分；
    输出按 BPM 递增、相邻差值受控的排序；考虑 crossfade 重叠的有效时长，逼近目标时长 ±5%。
    """

    def __init__(
        self,
        crossfade_seconds: float = 8.0,
        max_bpm_jump: float = DEFAULT_MAX_BPM_JUMP,
    ) -> None:
        self._crossfade_seconds = crossfade_seconds
        self._max_bpm_jump = max_bpm_jump

    def select_tracks(
        self,
        plan: EpisodePlan,
        library: list[TrackWithMetadata],
        crossfade_seconds: Optional[float] = None,
    ) -> list[SelectedTrack]:
        """
        从 library 中选曲并排序，返回含时间线占位与有效时长的 SelectedTrack 列表。
        """
        cf = crossfade_seconds if crossfade_seconds is not None else self._crossfade_seconds
        target = plan.target_duration_seconds
        lo_target = target * (1 - TARGET_DURATION_TOLERANCE)
        hi_target = target * (1 + TARGET_DURATION_TOLERANCE)

        # 1. 按 BPM 过滤（若有 overall_bpm_range）
        if plan.overall_bpm_range:
            bpm_lo, bpm_hi = plan.overall_bpm_range
            filtered = [
                t
                for t in library
                if t.metadata.bpm is None or (bpm_lo <= t.metadata.bpm <= bpm_hi)
            ]
            if not filtered and library:
                logger.warning("BPM 过滤后无候选，放宽为全部曲目。")
                filtered = library
        else:
            filtered = list(library)

        # 2. 按 BPM 升序（None 放末尾）
        def _bpm_key(t: TrackWithMetadata) -> tuple[bool, float]:
            bpm = t.metadata.bpm
            return (bpm is None, bpm if bpm is not None else 0.0)

        sorted_lib = sorted(filtered, key=_bpm_key)

        # 3. 贪心选曲：BPM  continuity + 时长约束
        selected: list[TrackWithMetadata] = []
        current_duration = 0.0
        prev_bpm: Optional[float] = None

        for twm in sorted_lib:
            if current_duration >= hi_target:
                break
            dur = twm.metadata.duration_seconds
            bpm = twm.metadata.bpm

            # 相邻 BPM 差值检查（仅当二者均有 BPM 时）
            if prev_bpm is not None and bpm is not None:
                if abs(bpm - prev_bpm) > self._max_bpm_jump:
                    continue

            effective = dur - cf if selected else dur
            if effective <= 0:
                continue
            if current_duration + effective > hi_target:
                continue

            selected.append(twm)
            current_duration += effective
            prev_bpm = bpm

            if current_duration >= lo_target:
                break

        # 4. 构建 SelectedTrack 列表（含时间线与 effective_duration）
        # crossfade 下：第 i 首在 (前 i-1 首有效时长累计) 处开始，即 sum(d_j) - (i-1)*cf
        result: list[SelectedTrack] = []
        t_acc = 0.0  # 下一首的起始位置 = 前一首的 start + duration - cf
        for i, twm in enumerate(selected):
            dur = twm.metadata.duration_seconds
            start = t_acc
            end = start + dur
            effective = dur - cf if i > 0 else dur
            t_acc = end - cf
            result.append(
                SelectedTrack(
                    track=twm.track,
                    start_time_in_episode=start,
                    end_time_in_episode=end,
                    effective_duration=effective,
                ),
            )

        logger.info(
            "选曲完成: %d 首，总有效时长 %.1fs（目标 %.1fs）",
            len(result),
            t_acc,
            target,
        )
        return result

"""
混音与时间线渲染：v1.1 时间线为 串词1 → 组1 → 串词2 → 组2 → … → 串词N → 组N；
组内歌曲 crossfade，组与串词之间不 crossfade，主持期间无背景音乐。

v1.3：有 plan 时按 plan 的 segment 拆分曲目组，确保 串词_i 与 segment_i 歌曲组严格对齐。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.models import AudioRenderConfig, EpisodePlan, SelectedTrack, VoiceoverSegment
from podcast_ai.infra.audio_backend import (
    crossfade_concat,
    export_audio,
    load_audio,
    simple_normalize,
)
from podcast_ai.modules.selection.selector import split_tracks_by_plan

logger = logging.getLogger(__name__)


@dataclass
class MixRenderSummary:
    """混音渲染摘要。"""

    mix_path: Path
    actual_duration_seconds: float
    track_count: int
    voiceover_count: int


def _split_tracks_into_groups(
    tracks: list[SelectedTrack],
    n_groups: int,
) -> list[list[SelectedTrack]]:
    """
    将 selected_tracks 均分为 n_groups 组，尽量均匀；用于 v1.1 时间线对齐。
    """
    if n_groups <= 0 or not tracks:
        return [list(tracks)] if tracks else []
    total = len(tracks)
    base_size = total // n_groups
    remainder = total % n_groups
    groups: list[list[SelectedTrack]] = []
    idx = 0
    for i in range(n_groups):
        size = base_size + (1 if i < remainder else 0)
        groups.append(tracks[idx : idx + size])
        idx += size
    return groups


class Mixer:
    """
    v1.1：构建时间线 串词1 → 组1 → 串词2 → 组2 → … → 串词N → 组N。
    组内歌曲 crossfade 转场，组与串词之间直接拼接（无 crossfade），主持期间无背景音乐。
    """

    def build_mix(
        self,
        selected_tracks: list[SelectedTrack],
        voiceovers: list[VoiceoverSegment],
        config: AudioRenderConfig,
        output_path: Path,
        plan: Optional[EpisodePlan] = None,
    ) -> MixRenderSummary:
        """
        执行混音并输出中间文件。
        - 有 voiceovers：按 串词_i → 组_i 顺序拼接；有 plan 时（v1.3）按 plan 的 segment 拆分曲目
        - 无 voiceovers：向后兼容，全部曲目 crossfade 拼接
        """
        cf = config.crossfade_seconds
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not selected_tracks:
            raise ValueError("selected_tracks 不能为空。")

        n = len(voiceovers)
        if n == 0:
            # 向后兼容：无主持时，全部曲目 crossfade 拼接
            track_audios = [
                simple_normalize(load_audio(st.track.file_path), target_dbfs=-20.0)
                for st in selected_tracks
            ]
            mix = crossfade_concat(track_audios, cf)
        else:
            # 串词1 → 组1 → 串词2 → 组2 → …；v1.3 有 plan 时按 segment 拆分，否则均分
            if plan is not None:
                track_groups = split_tracks_by_plan(plan, selected_tracks)
            else:
                track_groups = _split_tracks_into_groups(selected_tracks, n)
            parts: list[AudioSegment] = []

            for i in range(n):
                # 串词 i：主持期间无背景音乐，单独拼接
                try:
                    vo_audio = load_audio(voiceovers[i].audio_path)
                    vo_audio = simple_normalize(vo_audio, target_dbfs=-16.0)
                    parts.append(vo_audio)
                    logger.debug("拼接主持: %s", voiceovers[i].segment_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("加载主持 %s 失败，使用占位: %s", voiceovers[i].segment_id, exc)
                    parts.append(AudioSegment.silent(duration=0))

                # 组 i：组内 crossfade
                group = track_groups[i]
                if group:
                    group_audios = [
                        simple_normalize(load_audio(st.track.file_path), target_dbfs=-20.0)
                        for st in group
                    ]
                    group_mix = crossfade_concat(group_audios, cf)
                    parts.append(group_mix)
                else:
                    parts.append(AudioSegment.silent(duration=0))

            # 段与段之间不 crossfade（串词与组之间、组与串词之间）
            mix = crossfade_concat(parts, crossfade_seconds=0.0)

        # 导出
        export_audio(mix, output_path, format=output_path.suffix.lstrip(".") or "wav")

        duration_sec = len(mix) / 1000.0
        logger.info(
            "混音完成: %s，时长 %.1fs，曲目 %d 首，主持 %d 段",
            output_path,
            duration_sec,
            len(selected_tracks),
            len(voiceovers),
        )
        return MixRenderSummary(
            mix_path=output_path,
            actual_duration_seconds=duration_sec,
            track_count=len(selected_tracks),
            voiceover_count=len(voiceovers),
        )

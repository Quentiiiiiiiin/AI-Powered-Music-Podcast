"""
混音与时间线渲染：v1.1 时间线为 串词1 → 组1 → 串词2 → 组2 → … → 串词N → 组N；
组内歌曲 crossfade，组与串词之间不 crossfade，主持期间无背景音乐。

v1.3：有 plan 时按 plan 的 segment 拆分曲目组，确保 串词_i 与 segment_i 歌曲组严格对齐。

v2.0（试验）：相邻「主持段」与「音乐段」之间用 `voice_music_crossfade_seconds` 做短时重叠
（前段尾部淡出 + 后段头部淡入）；中段仍为独奏/独曲。歌曲-歌曲仍仅用 `crossfade_seconds`。
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


def _voice_music_crossfade_join(a: AudioSegment, b: AudioSegment, vm_ms: int) -> AudioSegment:
    """
    v2.0：串词↔音乐边界短时 crossfade。

    - a 末尾 vm_ms：fade_out；b 开头 vm_ms：fade_in；同长度叠加以避免硬切。
    - 总时长 = len(a) + len(b) - vm_ms（与简单串接相比缩短 vm_ms）。
    - vm_ms==0、或任一侧为空、或有效窗口为 0：退化为 a + b。
    """
    if vm_ms <= 0 or len(a) == 0 or len(b) == 0:
        return a + b
    vm_ms = min(vm_ms, len(a), len(b))
    if vm_ms <= 0:
        return a + b
    a_pre = a[:-vm_ms]
    a_tail = a[-vm_ms:].fade_out(vm_ms)
    b_head = b[:vm_ms].fade_in(vm_ms)
    b_rest = b[vm_ms:]
    overlap = a_tail.overlay(b_head)
    return a_pre + overlap + b_rest


def _concat_parts_with_voice_music_crossfade(
    parts: list[AudioSegment],
    voice_music_crossfade_seconds: float,
) -> AudioSegment:
    """将已排序的 [串词, 组, 串词, 组, …] 用边界 crossfade 串联。"""
    if not parts:
        raise ValueError("parts 不能为空。")
    vm_ms = int(round(voice_music_crossfade_seconds * 1000))
    if vm_ms <= 0 or len(parts) == 1:
        return crossfade_concat(parts, crossfade_seconds=0.0)
    acc = parts[0]
    for nxt in parts[1:]:
        acc = _voice_music_crossfade_join(acc, nxt, vm_ms)
    return acc


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
    v1.1 / v1.3：时间线 串词1 → 组1 → … → 串词N → 组N；组内歌曲 crossfade。
    v2.0：串词与相邻音乐段边界使用 `voice_music_crossfade_seconds` 短时叠化；串词中段无背景乐。
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
        - v2.0：段间（串词↔音乐）使用 `voice_music_crossfade_seconds`；置 0 等同 v1.1 硬拼
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

            mix = _concat_parts_with_voice_music_crossfade(
                parts,
                config.voice_music_crossfade_seconds,
            )

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

"""
混音与时间线渲染：按固定 crossfade 拼接曲目，将主持语音按策略叠入，输出中间混音文件。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.models import AudioRenderConfig, SelectedTrack, VoiceoverSegment
from podcast_ai.infra.audio_backend import (
    crossfade_concat,
    export_audio,
    load_audio,
    simple_normalize,
)

logger = logging.getLogger(__name__)


@dataclass
class MixRenderSummary:
    """混音渲染摘要。"""

    mix_path: Path
    actual_duration_seconds: float
    track_count: int
    voiceover_count: int


class Mixer:
    """
    构建「歌曲 + 主持」的统一时间线；
    按固定 crossfade 拼接曲目，将主持语音按 insert_time_in_episode 叠入。
    """

    def build_mix(
        self,
        selected_tracks: list[SelectedTrack],
        voiceovers: list[VoiceoverSegment],
        config: AudioRenderConfig,
        output_path: Path,
    ) -> MixRenderSummary:
        """
        执行混音并输出中间文件。
        """
        cf = config.crossfade_seconds
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not selected_tracks:
            raise ValueError("selected_tracks 不能为空。")

        # 1. 加载曲目音频并按 crossfade 拼接
        track_audios: list[AudioSegment] = []
        for st in selected_tracks:
            seg = load_audio(st.track.file_path)
            seg = simple_normalize(seg, target_dbfs=-20.0)
            track_audios.append(seg)

        music_mix = crossfade_concat(track_audios, cf)
        total_ms = len(music_mix)

        # 2. 将主持语音叠入
        for v in sorted(voiceovers, key=lambda x: x.insert_time_in_episode):
            try:
                vo_audio = load_audio(v.audio_path)
                vo_audio = simple_normalize(vo_audio, target_dbfs=-16.0)
                pos_ms = int(v.insert_time_in_episode * 1000)
                if pos_ms < 0:
                    pos_ms = 0
                if pos_ms + len(vo_audio) > total_ms:
                    music_mix = music_mix.append(
                        AudioSegment.silent(duration=pos_ms + len(vo_audio) - total_ms),
                        crossfade=0,
                    )
                    total_ms = len(music_mix)
                music_mix = music_mix.overlay(vo_audio, position=pos_ms)
                logger.debug("叠入主持: %s @ %.1fs", v.segment_id, v.insert_time_in_episode)
            except Exception as exc:  # noqa: BLE001
                logger.warning("叠入主持失败 %s，跳过: %s", v.segment_id, exc)

        # 3. 导出
        export_audio(music_mix, output_path, format=output_path.suffix.lstrip(".") or "wav")

        duration_sec = len(music_mix) / 1000.0
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

"""
主持串词与语音生成：从 EpisodePlan.segments[].host_script 生成 TTS 音频，输出 VoiceoverSegment 列表供混音使用。

v1.1：保证 voiceovers 与 plan.segments 一一对应（len(voiceovers) == len(plan.segments)），
对无 host_script 的 segment 输出占位（零时长静音），使混音可按索引对齐「串词_i → 组_i」。
v1.3：可选接收 segment_boundaries，将 insert_time_in_episode 设为 segment_i.music_start 之前的边界
（即 串词_1=0，串词_i=boundaries[i-1].music_end）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.models import EpisodePlan, SegmentBoundary, VoiceoverSegment
from podcast_ai.infra.audio_backend import export_audio
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.storage.paths import get_tts_cache_dir
from podcast_ai.infra.tts_client import TTSClient, get_default_tts_client

logger = logging.getLogger(__name__)

# 语言 -> edge-tts 默认发音人（当 config 未指定 voice 时）
_DEFAULT_VOICE_BY_LANG = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-JennyNeural",
}


def _get_placeholder_audio_path(output_dir: Path) -> Path:
    """
    返回零时长静音占位文件的路径；若不存在则创建。
    用于无 host_script 的 segment，保证 VoiceoverSegment 与 plan.segments 一一对应。
    """
    cache_dir = get_tts_cache_dir(output_dir)
    placeholder_path = cache_dir / "_placeholder" / "silent.wav"
    if placeholder_path.exists():
        return placeholder_path
    placeholder_path.parent.mkdir(parents=True, exist_ok=True)
    # 1ms 静音，兼容各类播放器；0ms 可能导致部分格式异常
    silent = AudioSegment.silent(duration=1)
    export_audio(silent, placeholder_path, format="wav")
    return placeholder_path


class VoiceoverService:
    """
    从 EpisodePlan 的 host_script 生成语音文件，并输出 VoiceoverSegment 列表。
    v1.3：当传入 segment_boundaries 时，insert_time 严格对齐 segment 实际歌曲边界。
    """

    def __init__(
        self,
        tts_client: Optional[TTSClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._tts = tts_client or get_default_tts_client(self._settings)

    def generate_voiceovers(
        self,
        plan: EpisodePlan,
        language: str = "zh",
        use_cache: bool = True,
        segment_boundaries: Optional[list[SegmentBoundary]] = None,
    ) -> list[VoiceoverSegment]:
        """
        为 plan.segments 一一对应生成 VoiceoverSegment 列表。
        - 有 host_script：调用 TTS 生成语音
        - 无 host_script：使用零时长静音占位
        - segment_boundaries 提供时（v1.3）：insert_time 严格对齐 segment 实际边界，
          串词_i 插入在 segment_i.music_start 之前（串词_1=0，串词_i=boundaries[i-1].music_end）
        - 未提供时：沿用 target_duration_seconds 累加（向后兼容）
        """
        voice = _get_voice_for_language(language, self._settings)
        output_dir = Path(self._settings.app.output_dir)
        placeholder_path = _get_placeholder_audio_path(output_dir)
        results: list[VoiceoverSegment] = []

        for idx, seg in enumerate(plan.segments):
            segment_id = seg.name or f"seg_{idx}"
            has_script = bool(seg.host_script and seg.host_script.strip())

            # v1.3：优先使用实际边界；否则按 target_duration 累加
            if segment_boundaries is not None and len(segment_boundaries) > idx:
                insert_time = segment_boundaries[idx - 1].music_end if idx > 0 else 0.0
            else:
                insert_time = sum(s.target_duration_seconds for s in plan.segments[:idx])

            if has_script:
                try:
                    audio_path = self._tts.synthesize(
                        seg.host_script,
                        voice=voice,
                        use_cache=use_cache,
                    )
                    results.append(
                        VoiceoverSegment(
                            segment_id=segment_id,
                            text=seg.host_script,
                            audio_path=audio_path,
                            insert_time_in_episode=insert_time,
                        ),
                    )
                    logger.debug("已生成语音: %s @ %.1fs", segment_id, insert_time)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("段 '%s' 语音生成失败，使用占位: %s", seg.name, exc)
                    results.append(
                        VoiceoverSegment(
                            segment_id=segment_id,
                            text="",
                            audio_path=placeholder_path,
                            insert_time_in_episode=insert_time,
                        ),
                    )
            else:
                results.append(
                    VoiceoverSegment(
                        segment_id=segment_id,
                        text="",
                        audio_path=placeholder_path,
                        insert_time_in_episode=insert_time,
                    ),
                )

        logger.info("主持语音生成完成: %d 段（与 segments 一一对应）", len(results))
        return results


def _get_voice_for_language(language: str, settings: Settings) -> str:
    """根据 language 与配置返回 TTS voice；优先使用 config，否则按语言选择默认。"""
    if settings.tts.voice and settings.tts.voice.strip():
        return settings.tts.voice.strip()
    return _DEFAULT_VOICE_BY_LANG.get(language, "zh-CN-XiaoxiaoNeural")

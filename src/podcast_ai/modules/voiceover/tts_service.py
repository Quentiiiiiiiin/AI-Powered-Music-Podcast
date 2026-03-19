"""
主持串词与语音生成：从 EpisodePlan.segments[].host_script 生成 TTS 音频，输出 VoiceoverSegment 列表供混音使用。

v1.1：保证 voiceovers 与 plan.segments 一一对应（len(voiceovers) == len(plan.segments)），
对无 host_script 的 segment 输出占位（零时长静音），使混音可按索引对齐「串词_i → 组_i」。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.models import EpisodePlan, VoiceoverSegment
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
    从 EpisodePlan 的 host_script 生成语音文件，并输出带插入策略的 VoiceoverSegment 列表。
    插入时间为各段落在节目时间线中的起始位置（基于 plan 的 target_duration 累加）。
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
    ) -> list[VoiceoverSegment]:
        """
        为 plan.segments 一一对应生成 VoiceoverSegment 列表（v1.1）。
        - 有 host_script：调用 TTS 生成语音
        - 无 host_script：使用零时长静音占位，保证 len(voiceovers) == len(plan.segments)
        insert_time_in_episode 为该段落在节目时间线中的起始秒数（基于 plan 的 target_duration）。
        """
        voice = _get_voice_for_language(language, self._settings)
        output_dir = Path(self._settings.app.output_dir)
        placeholder_path = _get_placeholder_audio_path(output_dir)
        segment_start = 0.0
        results: list[VoiceoverSegment] = []

        for idx, seg in enumerate(plan.segments):
            segment_id = seg.name or f"seg_{idx}"
            has_script = bool(seg.host_script and seg.host_script.strip())

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
                            insert_time_in_episode=segment_start,
                        ),
                    )
                    logger.debug("已生成语音: %s @ %.1fs", segment_id, segment_start)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("段 '%s' 语音生成失败，使用占位: %s", seg.name, exc)
                    results.append(
                        VoiceoverSegment(
                            segment_id=segment_id,
                            text="",
                            audio_path=placeholder_path,
                            insert_time_in_episode=segment_start,
                        ),
                    )
            else:
                # 无串词：占位，保证一一对应
                results.append(
                    VoiceoverSegment(
                        segment_id=segment_id,
                        text="",
                        audio_path=placeholder_path,
                        insert_time_in_episode=segment_start,
                    ),
                )

            segment_start += seg.target_duration_seconds

        logger.info("主持语音生成完成: %d 段（与 segments 一一对应）", len(results))
        return results


def _get_voice_for_language(language: str, settings: Settings) -> str:
    """根据 language 与配置返回 TTS voice；优先使用 config，否则按语言选择默认。"""
    if settings.tts.voice and settings.tts.voice.strip():
        return settings.tts.voice.strip()
    return _DEFAULT_VOICE_BY_LANG.get(language, "zh-CN-XiaoxiaoNeural")

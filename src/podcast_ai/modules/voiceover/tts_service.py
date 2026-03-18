"""
主持串词与语音生成：从 EpisodePlan.segments[].host_script 生成 TTS 音频，输出 VoiceoverSegment 列表供混音使用。
"""
from __future__ import annotations

import logging
from typing import Optional

from podcast_ai.core.models import EpisodePlan, VoiceoverSegment
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.tts_client import TTSClient, get_default_tts_client

logger = logging.getLogger(__name__)

# 语言 -> edge-tts 默认发音人（当 config 未指定 voice 时）
_DEFAULT_VOICE_BY_LANG = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-JennyNeural",
}


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
        为 plan 中每个非空 host_script 生成 TTS 音频，并返回 VoiceoverSegment 列表。
        insert_time_in_episode 为该段落在节目时间线中的起始秒数（基于 plan 的 target_duration）。
        """
        voice = _get_voice_for_language(language, self._settings)
        segment_start = 0.0
        results: list[VoiceoverSegment] = []

        for idx, seg in enumerate(plan.segments):
            if not seg.host_script or not seg.host_script.strip():
                segment_start += seg.target_duration_seconds
                continue
            try:
                audio_path = self._tts.synthesize(
                    seg.host_script,
                    voice=voice,
                    use_cache=use_cache,
                )
                segment_id = seg.name or f"seg_{idx}"
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
                logger.warning("段 '%s' 语音生成失败，跳过: %s", seg.name, exc)
            finally:
                segment_start += seg.target_duration_seconds

        logger.info("主持语音生成完成: %d 段", len(results))
        return results


def _get_voice_for_language(language: str, settings: Settings) -> str:
    """根据 language 与配置返回 TTS voice；优先使用 config，否则按语言选择默认。"""
    if settings.tts.voice and settings.tts.voice.strip():
        return settings.tts.voice.strip()
    return _DEFAULT_VOICE_BY_LANG.get(language, "zh-CN-XiaoxiaoNeural")

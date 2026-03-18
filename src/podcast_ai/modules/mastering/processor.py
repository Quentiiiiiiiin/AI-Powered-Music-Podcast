"""
母带处理：对混音结果做整体 loudness 标准化（ffmpeg loudnorm），输出最终可发布音频。
"""
from __future__ import annotations

import logging
from pathlib import Path

from podcast_ai.core.models import AudioRenderConfig
from podcast_ai.infra.audio_backend import loudness_normalize_ffmpeg

logger = logging.getLogger(__name__)


class MasteringService:
    """
    对混音文件应用 loudness 标准化，避免削波并保持输出可发布。
    """

    def apply_mastering(
        self,
        mix_path: Path,
        output_path: Path,
        config: AudioRenderConfig,
    ) -> Path:
        """
        使用 ffmpeg loudnorm 对 mix_path 做响度标准化，输出至 output_path。
        若 output_path 为 .mp3，将使用 config.bitrate。
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bitrate = config.bitrate if output_path.suffix.lower() == ".mp3" else None

        loudness_normalize_ffmpeg(
            input_path=mix_path,
            output_path=output_path,
            target_lufs=config.loudness_target_lufs,
            bitrate=bitrate,
        )
        logger.info("母带完成: %s -> %s (LUFS %.1f)", mix_path, output_path, config.loudness_target_lufs)
        return output_path

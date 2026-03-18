from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, Optional

import edge_tts  # type: ignore[import-untyped]

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.core.models import VoiceoverSegment
from podcast_ai.infra.config import Settings, TTSConfig, load_settings
from podcast_ai.infra.storage.paths import (
    get_tts_cache_dir,
    get_tts_cache_file,
)

logger = logging.getLogger(__name__)


class TTSClient(ABC):
    """TTSClient 抽象接口。"""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,
        use_cache: bool = True,
    ) -> Path:
        """
        将文本转换为语音文件并返回文件路径。

        - voice/language 可选；若为空则使用默认配置
        - use_cache 为 True 时，同一文本 + voice 会命中缓存
        """


class EdgeTTSSimpleClient(TTSClient):
    """
    基于 edge-tts 的默认 TTS 实现（MVP）。

    - 使用 Settings.app.output_dir 下的 cache/tts 作为缓存目录
    - 不做复杂的发音人/语言管理，只透传配置中的 voice
    """

    def __init__(self, cfg: TTSConfig, settings: Settings) -> None:
        self._cfg = cfg
        self._settings = settings

    def _get_cache_path(self, text: str, voice: str) -> Path:
        output_dir = Path(self._settings.app.output_dir)
        cache_dir = get_tts_cache_dir(output_dir)
        h = hashlib.sha256()
        h.update(voice.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        h.update(text.encode("utf-8", errors="ignore"))
        text_hash = h.hexdigest()[:32]
        return get_tts_cache_file(cache_dir, voice=voice, text_hash=text_hash, ext="mp3")

    async def _synthesize_once_async(self, text: str, voice: str, dest: Path) -> None:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await communicate.save(str(dest))

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,  # noqa: ARG002
        use_cache: bool = True,
    ) -> Path:
        import asyncio

        effective_voice = voice or self._cfg.voice
        if not effective_voice:
            raise AIServiceError("TTS voice 未配置。请在 config.yaml 或环境变量中设置 tts.voice。")

        cache_path = self._get_cache_path(text, effective_voice)
        if use_cache and cache_path.exists():
            logger.debug("命中 TTS 缓存：%s", cache_path)
            return cache_path

        last_error: Optional[Exception] = None
        max_retries = max(self._cfg.max_retries, 1)
        for attempt in range(1, max_retries + 1):
            try:
                with log_timing(logger, f"tts_edge_attempt_{attempt}"):
                    asyncio.run(self._synthesize_once_async(text, effective_voice, cache_path))
                return cache_path
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("TTS 调用失败（第 %d 次）：%s", attempt, repr(exc))

        raise AIServiceError("TTS 多次重试后仍然失败。") from last_error


def get_default_tts_client(settings: Settings | None = None) -> TTSClient:
    """
    基于全局 Settings 返回一个默认 TTSClient 实例。

    - 当前 provider 支持：edge_tts
    """
    s = settings or load_settings()
    cfg = s.tts
    if cfg.provider == "edge_tts":
        return EdgeTTSSimpleClient(cfg, s)
    raise AIServiceError(f"暂不支持的 TTS provider：{cfg.provider}")


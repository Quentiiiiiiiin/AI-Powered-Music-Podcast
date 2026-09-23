"""
本地音乐库扫描与元数据提取；支持缓存与增量判定。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from podcast_ai.core.models import Track, TrackMetadata, TrackWithMetadata
from podcast_ai.infra.audio_backend import estimate_bpm, get_duration_seconds
from podcast_ai.infra.config import CacheConfig, Settings, load_settings
from podcast_ai.infra.storage.cache import (
    get_library_cache_path,
    load_library_cache,
    save_library_cache,
)

logger = logging.getLogger(__name__)

# 支持的音频扩展名（小写）
_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac"}


def _first_value(val: Optional[object]) -> Optional[str]:
    """从 mutagen easy 接口返回的 list 或单值中取第一个字符串。"""
    if val is None:
        return None
    if isinstance(val, list) and val:
        v = val[0]
        return str(v) if v is not None else None
    return str(val) if isinstance(val, str) else None


def _read_tags(path: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """使用 mutagen 读取 title、artist、genre；读取失败时返回 (None, None, None)。"""
    try:
        import mutagen  # type: ignore[import-untyped]
    except ImportError:
        return None, None, None
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return None, None, None
        title = _first_value(f.get("title"))
        artist = _first_value(f.get("artist"))
        genre = _first_value(f.get("genre"))
        return title, artist, genre
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取标签失败 %s: %s", path, exc)
        return None, None, None


def _track_id(path: Path) -> str:
    """基于文件绝对路径生成稳定 track id。"""
    h = hashlib.sha256()
    h.update(str(path.resolve()).encode("utf-8", errors="ignore"))
    return f"tr_{h.hexdigest()[:12]}"


def _collect_audio_paths(root: Path) -> dict[Path, float]:
    """递归收集 root 下所有 .mp3/.wav 的 path -> mtime。"""
    out: dict[Path, float] = {}
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in _AUDIO_SUFFIXES:
                try:
                    out[p] = p.stat().st_mtime
                except OSError:  # noqa: BLE001
                    continue
    except OSError as exc:  # noqa: BLE001
        logger.warning("扫描目录失败 %s: %s", root, exc)
    return out


class LibraryScanner:
    """
    扫描指定目录下的 mp3/wav/flac，提取基础标签与时长，可选 BPM；
    支持 JSON 缓存与按文件 mtime 的增量判定。
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        cache_config: Optional[CacheConfig] = None,
        bpm_max_seconds: Optional[float] = 30.0,
    ) -> None:
        self._settings = settings or load_settings()
        self._cache_config = cache_config or self._settings.cache
        # BPM 只分析前 N 秒以提速；None 表示不计算 BPM
        self._bpm_max_seconds = bpm_max_seconds

    def scan_library(self, root_dir: Path) -> list[TrackWithMetadata]:
        """
        扫描 root_dir 下所有 mp3/wav/flac，提取标签、时长与可选 BPM。
        """
        paths_with_mtime = _collect_audio_paths(root_dir)
        results: list[TrackWithMetadata] = []
        for path in sorted(paths_with_mtime.keys()):
            try:
                track_id = _track_id(path)
                title, artist, genre = _read_tags(path)
                duration = get_duration_seconds(path)
                bpm = None
                if self._bpm_max_seconds is not None and self._bpm_max_seconds > 0:
                    bpm = estimate_bpm(path, max_seconds=self._bpm_max_seconds)
                track = Track(
                    id=track_id,
                    file_path=path,
                    title=title,
                    artist=artist,
                )
                metadata = TrackMetadata(
                    track_id=track_id,
                    duration_seconds=duration,
                    bpm=bpm,
                    genre=genre,
                )
                results.append(TrackWithMetadata(track=track, metadata=metadata))
            except Exception as exc:  # noqa: BLE001
                logger.warning("跳过文件 %s: %s", path, exc)
                continue
        logger.info("扫描完成: %s，共 %d 首", root_dir, len(results))
        return results

    def scan_or_load_cache(self, root_dir: Path) -> list[TrackWithMetadata]:
        """
        若启用缓存且当前目录的文件 mtime 与缓存一致则返回缓存；
        否则执行 scan_library 并更新缓存。
        """
        root_dir = root_dir.resolve()
        current_states = _collect_audio_paths(root_dir)
        if not current_states:
            return []

        cache_dir = Path(self._cache_config.dir)
        cache_path = get_library_cache_path(cache_dir, root_dir)

        if self._cache_config.enabled and cache_path.exists():
            loaded = load_library_cache(cache_path)
            if loaded is not None:
                cached_tracks, cached_states = loaded
                if cached_states == current_states:
                    logger.debug("使用库缓存: %s (%d 条)", cache_path, len(cached_tracks))
                    return cached_tracks
        tracks = self.scan_library(root_dir)
        file_states = _collect_audio_paths(root_dir)
        if self._cache_config.enabled:
            save_library_cache(cache_path, tracks, file_states)
        return tracks

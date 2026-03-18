"""
音乐库扫描结果缓存（JSON），支持按目录缓存与增量判定。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from podcast_ai.core.models import TrackWithMetadata

logger = logging.getLogger(__name__)

CACHE_VERSION = 1


def _cache_key(music_dir: Path) -> str:
    """基于音乐目录绝对路径生成稳定缓存键。"""
    abs_path = str(music_dir.resolve())
    return hashlib.sha256(abs_path.encode("utf-8", errors="ignore")).hexdigest()[:16]


def get_library_cache_path(cache_dir: Path, music_dir: Path) -> Path:
    """
    返回该音乐目录对应的缓存文件路径。
    {cache_dir}/library/{cache_key}/tracks.json
    """
    key = _cache_key(music_dir)
    return cache_dir / "library" / key / "tracks.json"


def load_library_cache(
    cache_path: Path,
) -> tuple[list[TrackWithMetadata], dict[Path, float]] | None:
    """
    从 JSON 加载缓存的曲目列表与文件状态（path -> mtime）。
    文件不存在或格式错误时返回 None。
    """
    if not cache_path.exists():
        return None
    try:
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取缓存失败 %s: %s", cache_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    tracks_data = data.get("tracks")
    file_states_data = data.get("file_states") or {}
    if not isinstance(tracks_data, list):
        return None
    if not isinstance(file_states_data, dict):
        file_states_data = {}
    tracks: list[TrackWithMetadata] = []
    for item in tracks_data:
        try:
            tracks.append(TrackWithMetadata.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            logger.debug("跳过无效缓存条目: %s", exc)
            continue
    file_states = {}
    for p_str, mtime in file_states_data.items():
        try:
            file_states[Path(p_str)] = float(mtime)
        except Exception:  # noqa: BLE001
            continue
    return tracks, file_states


def save_library_cache(
    cache_path: Path,
    tracks: list[TrackWithMetadata],
    file_states: dict[Path, float],
) -> None:
    """将曲目列表与文件状态写入 JSON 缓存。"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tracks_data = [t.model_dump(mode="json") for t in tracks]
    file_states_data = {str(p): m for p, m in file_states.items()}
    data = {
        "version": CACHE_VERSION,
        "tracks": tracks_data,
        "file_states": file_states_data,
    }
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.debug("已保存库缓存: %s (%d 条)", cache_path, len(tracks))

"""Console 参数快照：仅保存表单字段，不写入 API Key。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

PRESET_SCHEMA = "podcast-ai.console_preset.v1"
PRESET_DIRNAME = "console_presets"

# 与 Console 表单字段对齐；后续 Preset/Experiment 可沿用同一 schema 名。
PRESET_KEYS: tuple[str, ...] = (
    "topic",
    "duration_minutes",
    "language",
    "agent_mode",
    "output_dir",
    "llm_model",
    "openrouter_provider",
    "llm_base_url",
    "snapshot_path",
    "music_dir",
    "tts_provider",
    "mix_params_path",
    "crossfade_seconds",
    "voice_music_crossfade_seconds",
    "intro_align_enabled",
    "intro_align_max_seconds",
)

_SECRET_KEY_RE = re.compile(r"(api_key|apikey|secret|token|password)", re.IGNORECASE)
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def presets_dir(output_dir: str | Path) -> Path:
    return Path(output_dir).expanduser() / PRESET_DIRNAME


def list_preset_names(output_dir: str | Path) -> list[str]:
    folder = presets_dir(output_dir)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if p.is_file())


def _safe_stem(name: str) -> str:
    raw = str(name).strip()
    if not raw:
        raise ValueError("快照名称不能为空。")
    if _UNSAFE_NAME_RE.search(raw) or raw in {".", ".."}:
        raise ValueError(f"快照名称含非法字符：{name!r}")
    stem = Path(raw).stem.strip()
    if not stem or stem in {".", ".."}:
        raise ValueError("快照名称不能为空。")
    return stem


def build_snapshot(values: Mapping[str, Any]) -> dict[str, Any]:
    """从表单 dict 构建可落盘快照（丢弃未知字段与疑似密钥）。"""
    payload: dict[str, Any] = {"schema": PRESET_SCHEMA}
    for key in PRESET_KEYS:
        if key not in values:
            continue
        if _SECRET_KEY_RE.search(key):
            continue
        payload[key] = values[key]
    return payload


def save_preset(output_dir: str | Path, name: str, values: Mapping[str, Any]) -> Path:
    folder = presets_dir(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe_stem(name)}.json"
    text = json.dumps(build_snapshot(values), ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return path


def load_preset(path: str | Path) -> dict[str, Any]:
    """读取快照；非法 JSON 或非 object 时抛出 ValueError。"""
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"快照文件不存在：{file_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"快照不是合法 JSON：{file_path}（{exc}）") from exc
    except OSError as exc:
        raise ValueError(f"读取快照失败：{file_path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"快照顶层必须是 JSON object：{file_path}")
    return {k: raw[k] for k in PRESET_KEYS if k in raw}


def load_preset_by_name(output_dir: str | Path, name: str) -> dict[str, Any]:
    stem = _safe_stem(name)
    path = presets_dir(output_dir) / f"{stem}.json"
    return load_preset(path)

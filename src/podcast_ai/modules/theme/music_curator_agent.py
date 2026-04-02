from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.prompts import build_music_curator_agent_messages
from podcast_ai.modules.theme.state import PlanState, assert_plan_state_valid, merge_plan_state

logger = logging.getLogger(__name__)

_CURATOR_SEGMENT_ALLOWED_KEYS = {"segment_id", "playlist"}
_PLAYLIST_ALLOWED_KEYS = {"track", "artist", "bpm"}


def _normalize_llm_json_raw(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _sanitize_curator_patch(data: Dict[str, Any], *, expected_segments: int) -> Dict[str, Any]:
    if set(data.keys()) - {"segments"}:
        forbidden_top = sorted(set(data.keys()) - {"segments"})
        raise AIServiceError(f"Music Curator 越权写入顶层字段：{', '.join(forbidden_top)}。")

    segments = data.get("segments")
    if not isinstance(segments, list):
        raise AIServiceError("Music Curator 输出中 segments 必须是数组。")
    if len(segments) != expected_segments:
        raise AIServiceError(
            f"Music Curator 输出 segments 长度不匹配：expected {expected_segments}, got {len(segments)}。",
        )

    sanitized_segments: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise AIServiceError(f"segments[{idx}] 必须是对象。")
        forbidden = [k for k in seg.keys() if k not in _CURATOR_SEGMENT_ALLOWED_KEYS]
        if forbidden:
            raise AIServiceError(f"Music Curator 越权写入 segments[{idx}] 字段：{', '.join(forbidden)}。")

        playlist = seg.get("playlist")
        if not isinstance(playlist, list):
            raise AIServiceError(f"segments[{idx}].playlist 必须是数组。")

        sanitized_playlist: List[Dict[str, Any]] = []
        for item_idx, item in enumerate(playlist):
            if not isinstance(item, dict):
                raise AIServiceError(f"segments[{idx}].playlist[{item_idx}] 必须是对象。")
            item_forbidden = [k for k in item.keys() if k not in _PLAYLIST_ALLOWED_KEYS]
            if item_forbidden:
                raise AIServiceError(
                    f"segments[{idx}].playlist[{item_idx}] 越权字段：{', '.join(item_forbidden)}。",
                )
            sanitized_playlist.append({k: item[k] for k in item.keys() if k in _PLAYLIST_ALLOWED_KEYS})

        segment_patch: Dict[str, Any] = {"playlist": sanitized_playlist}
        if "segment_id" in seg:
            segment_patch["segment_id"] = seg["segment_id"]
        sanitized_segments.append(segment_patch)

    return {"segments": sanitized_segments}


class MusicCuratorAgent:
    """v3.0 Music Curator Agent：为各段补齐 segments[*].playlist。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._llm = llm_client or get_default_llm_client(self._settings)

    def run(self, state: PlanState) -> PlanState:
        assert_plan_state_valid(state)
        segments_count = len(state.get("segments", []))
        if segments_count == 0:
            raise AIServiceError("Music Curator：state.segments 为空，无法生成 playlist。")

        messages = build_music_curator_agent_messages(state)

        try:
            raw = self._llm.generate(messages, temperature=0.4)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Music Curator Agent 失败：{exc!r}") from exc

        logger.debug("Music Curator Agent raw output (truncated): %s", raw[:1000])

        try:
            data = json.loads(_normalize_llm_json_raw(raw))
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError("Music Curator Agent 返回结果不是有效 JSON。") from exc

        if not isinstance(data, dict):
            raise AIServiceError("Music Curator Agent 返回的 JSON 顶层必须是对象。")

        patch = _sanitize_curator_patch(data, expected_segments=segments_count)
        next_state = merge_plan_state(state, patch)
        next_state = merge_plan_state(next_state, {"control": {"last_updated_by": "Music Curator"}})
        assert_plan_state_valid(next_state)
        return next_state

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.modules.theme.agent_response_schemas import (
    build_music_curator_response_schema,
    build_openrouter_response_format,
)
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_MUSIC_CURATOR, PlanAuditSink
from podcast_ai.modules.theme.prompts import build_music_curator_agent_messages
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Music Curator Agent"

_CURATOR_SEGMENT_ALLOWED_KEYS = {"segment_id", "playlist"}
_PLAYLIST_ALLOWED_KEYS = {"track", "artist", "bpm"}


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
            bpm = item.get("bpm")
            if bpm is not None and not isinstance(bpm, int):
                raise AIServiceError(
                    f"segments[{idx}].playlist[{item_idx}].bpm 必须是整数或 null。",
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

    def run(
        self,
        state: PlanState,
        mode: str = "generation",
        *,
        audit_sink: PlanAuditSink | None = None,
        round_iteration: int | None = None,
    ) -> PlanState:
        segments_count = len(state.get("segments", []))
        if segments_count == 0:
            raise AIServiceError("Music Curator：state.segments 为空，无法生成 playlist。")

        messages = build_music_curator_agent_messages(state, mode)

        gen_kwargs: Dict[str, Any] = {"temperature": 0.4}
        structured = should_use_structured_output(self._settings.llm)
        if structured:
            gen_kwargs["response_format"] = build_openrouter_response_format(
                "podcast_music_curator_response",
                build_music_curator_response_schema(segments_count),
            )

        try:
            raw = self._llm.generate(messages, **gen_kwargs)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Music Curator Agent 失败：{exc!r}") from exc

        logger.debug("Music Curator Agent raw output (truncated): %s", raw[:1000])

        data = parse_agent_json_response(
            agent_label=AGENT_LABEL,
            raw=raw,
            state=state,
            structured=structured,
            allow_repair_fallback=not structured,
            output_dir=Path(self._settings.app.output_dir),
        )

        if audit_sink is not None and round_iteration is not None:
            audit_sink.write_agent_artifact(
                iteration=round_iteration,
                agent_slug=AUDIT_SLUG_MUSIC_CURATOR,
                mode=mode,
                request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                raw_llm_text=raw,
                parsed_patch=data,
            )

        patch = _sanitize_curator_patch(data, expected_segments=segments_count)
        next_state = merge_plan_state(state, patch)
        next_state = merge_plan_state(next_state, {"control": {"last_updated_by": "Music Curator"}})
        return next_state

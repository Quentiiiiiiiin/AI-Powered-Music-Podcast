from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.modules.theme.agent_response_schemas import (
    PLANNER_RESPONSE_SCHEMA,
    build_openrouter_response_format,
)
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.prompts import build_planner_agent_messages
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Planner Agent"

_PLANNER_SEGMENT_ALLOWED_KEYS = {
    "segment_id",
    "order",
    "name",
    "target_duration_seconds",
    "bpm_range",
    "mood",
    "segment_design",
}


def sanitize_planner_patch(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    只保留 Planner 合法写字段；发现越权字段直接报错，便于定位。

    v3.4：OpenRouter strict schema 要求根上四键始终存在；`null` 表示本键不合并（与结构化输出一致）。
    """
    patch: Dict[str, Any] = {}

    if "meta" in data:
        mv = data["meta"]
        if mv is None:
            pass
        elif not isinstance(mv, dict):
            raise AIServiceError("Planner 输出中 meta 必须是对象或 null。")
        else:
            meta_patch: Dict[str, Any] = {}
            if "theme_description" in mv:
                meta_patch["theme_description"] = mv["theme_description"]
            forbidden_meta = [k for k in mv.keys() if k != "theme_description"]
            if forbidden_meta:
                raise AIServiceError(f"Planner 越权写入 meta 字段：{', '.join(forbidden_meta)}。")
            if meta_patch:
                patch["meta"] = meta_patch

    if "global_constraints" in data:
        gv = data["global_constraints"]
        if gv is None:
            pass
        elif not isinstance(gv, dict):
            raise AIServiceError("Planner 输出中 global_constraints 必须是对象或 null。")
        else:
            patch["global_constraints"] = gv

    if "plan" in data:
        pv = data["plan"]
        if pv is None:
            pass
        elif not isinstance(pv, dict):
            raise AIServiceError("Planner 输出中 plan 必须是对象或 null。")
        else:
            plan_patch: Dict[str, Any] = {}
            if "segments_design" in pv:
                plan_patch["segments_design"] = pv["segments_design"]
            if "emotion_curve" in pv:
                plan_patch["emotion_curve"] = pv["emotion_curve"]
            if plan_patch:
                patch["plan"] = plan_patch

    if "segments" in data:
        sv = data["segments"]
        if sv is None:
            pass
        elif not isinstance(sv, list):
            raise AIServiceError("Planner 输出中 segments 必须是数组或 null。")
        else:
            sanitized_segments: List[Dict[str, Any]] = []
            for idx, segment in enumerate(sv):
                if not isinstance(segment, dict):
                    raise AIServiceError(f"Planner 输出中 segments[{idx}] 必须是对象。")
                forbidden = [k for k in segment.keys() if k not in _PLANNER_SEGMENT_ALLOWED_KEYS]
                if forbidden:
                    raise AIServiceError(
                        f"Planner 越权写入 segments[{idx}] 字段：{', '.join(forbidden)}。",
                    )
                sanitized_segments.append({k: segment[k] for k in segment.keys() if k in _PLANNER_SEGMENT_ALLOWED_KEYS})
            patch["segments"] = sanitized_segments

    forbidden_top = [k for k in data.keys() if k not in {"meta", "global_constraints", "plan", "segments"}]
    if forbidden_top:
        raise AIServiceError(f"Planner 越权写入顶层字段：{', '.join(forbidden_top)}。")

    return patch


class PlannerAgent:
    """v3.0 Planner Agent：负责全局结构与段落骨架。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._llm = llm_client or get_default_llm_client(self._settings)

    def run(self, state: PlanState, mode: str = "generation") -> PlanState:
        messages = build_planner_agent_messages(state, mode)

        gen_kwargs: Dict[str, Any] = {"temperature": 0.4}
        structured = should_use_structured_output(self._settings.llm)
        if structured:
            gen_kwargs["response_format"] = build_openrouter_response_format(
                "podcast_planner_response",
                PLANNER_RESPONSE_SCHEMA,
            )

        try:
            raw = self._llm.generate(messages, **gen_kwargs)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Planner Agent 失败：{exc!r}") from exc

        logger.debug("Planner Agent raw output (truncated): %s", raw[:1000])

        data = parse_agent_json_response(
            agent_label=AGENT_LABEL,
            raw=raw,
            state=state,
            structured=structured,
            allow_repair_fallback=not structured,
            output_dir=Path(self._settings.app.output_dir),
        )

        patch = sanitize_planner_patch(data)
        next_state = merge_plan_state(state, patch)
        next_state = merge_plan_state(
            next_state,
            {"control": {"last_updated_by": "Planner"}},
        )
        return next_state

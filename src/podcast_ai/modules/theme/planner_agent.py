from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.prompts import build_planner_agent_messages
from podcast_ai.modules.theme.state import PlanState, assert_plan_state_valid, merge_plan_state

logger = logging.getLogger(__name__)

_PLANNER_SEGMENT_ALLOWED_KEYS = {
    "segment_id",
    "order",
    "name",
    "target_duration_seconds",
    "bpm_range",
    "mood",
    "segment_design",
}


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


def sanitize_planner_patch(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    只保留 Planner 合法写字段；发现越权字段直接报错，便于定位。
    """
    patch: Dict[str, Any] = {}

    if "meta" in data:
        if not isinstance(data["meta"], dict):
            raise AIServiceError("Planner 输出中 meta 必须是对象。")
        meta_patch: Dict[str, Any] = {}
        if "theme_description" in data["meta"]:
            meta_patch["theme_description"] = data["meta"]["theme_description"]
        forbidden_meta = [k for k in data["meta"].keys() if k != "theme_description"]
        if forbidden_meta:
            raise AIServiceError(f"Planner 越权写入 meta 字段：{', '.join(forbidden_meta)}。")
        if meta_patch:
            patch["meta"] = meta_patch

    if "global_constraints" in data:
        if not isinstance(data["global_constraints"], dict):
            raise AIServiceError("Planner 输出中 global_constraints 必须是对象。")
        patch["global_constraints"] = data["global_constraints"]

    if "plan" in data:
        if not isinstance(data["plan"], dict):
            raise AIServiceError("Planner 输出中 plan 必须是对象。")
        plan_patch: Dict[str, Any] = {}
        if "segments_design" in data["plan"]:
            plan_patch["segments_design"] = data["plan"]["segments_design"]
        if "emotion_curve" in data["plan"]:
            plan_patch["emotion_curve"] = data["plan"]["emotion_curve"]
        if plan_patch:
            patch["plan"] = plan_patch

    if "segments" in data:
        if not isinstance(data["segments"], list):
            raise AIServiceError("Planner 输出中 segments 必须是数组。")
        sanitized_segments: List[Dict[str, Any]] = []
        for idx, segment in enumerate(data["segments"]):
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

    def run(self, state: PlanState) -> PlanState:
        assert_plan_state_valid(state)
        messages = build_planner_agent_messages(state)

        try:
            raw = self._llm.generate(messages, temperature=0.4)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Planner Agent 失败：{exc!r}") from exc

        logger.debug("Planner Agent raw output (truncated): %s", raw[:1000])

        try:
            data = json.loads(_normalize_llm_json_raw(raw))
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError("Planner Agent 返回结果不是有效 JSON。") from exc
        if not isinstance(data, dict):
            raise AIServiceError("Planner Agent 返回的 JSON 顶层必须是对象。")

        patch = sanitize_planner_patch(data)
        next_state = merge_plan_state(state, patch)
        next_state = merge_plan_state(
            next_state,
            {"control": {"last_updated_by": "Planner"}},
        )
        assert_plan_state_valid(next_state)
        return next_state

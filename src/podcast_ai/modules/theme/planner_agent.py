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
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_PLANNER, PlanAuditSink
from podcast_ai.modules.theme.prompts import build_planner_agent_messages
from podcast_ai.modules.theme.prompts_staged import build_planner_staged_messages
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Planner Agent"

# v6.1 Schema_Planner_v4：禁止 playlist/script；移除 bpm_range/mood/segment_design
_PLANNER_META_ALLOWED_KEYS = {
    "theme_description",
    "theme_type",
    "theme_subject",
    "theme_relationship",
}
_PLANNER_GLOBAL_REQUIRED_KEYS = ("energy_strategy", "sonic_world", "avoid")
_PLANNER_PLAN_REQUIRED_KEYS = ("segment_count", "episode_direction", "segments_design")
_PLANNER_SEGMENT_ALLOWED_KEYS = {
    "segment_id",
    "order",
    "name",
    "target_duration_seconds",
    "narrative_function",
    "scene",
    "sonic_direction",
    "lyrical_direction",
    "anchor_tracks",
    "reference_material",
    "sequence_direction",
    "transition_to_next",
}
_PLANNER_SEGMENT_REQUIRED_KEYS = tuple(_PLANNER_SEGMENT_ALLOWED_KEYS)


def _require_keys(obj: Dict[str, Any], keys: tuple[str, ...] | set[str], *, label: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise AIServiceError(f"Planner 输出缺少字段：{label} → {', '.join(missing)}")


def sanitize_planner_patch(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    只保留 Planner 合法写字段；发现越权字段直接报错，便于定位。

    v3.4：OpenRouter strict schema 要求根上四键始终存在；`null` 表示本键不合并。
    v6.1：对齐 Schema_Planner_v4；禁止 playlist / script / critic / control。
    """
    patch: Dict[str, Any] = {}

    if "meta" in data:
        mv = data["meta"]
        if mv is None:
            pass
        elif not isinstance(mv, dict):
            raise AIServiceError("Planner 输出中 meta 必须是对象或 null。")
        else:
            forbidden_meta = [k for k in mv.keys() if k not in _PLANNER_META_ALLOWED_KEYS]
            if forbidden_meta:
                raise AIServiceError(f"Planner 越权写入 meta 字段：{', '.join(forbidden_meta)}。")
            _require_keys(mv, _PLANNER_META_ALLOWED_KEYS, label="meta")
            patch["meta"] = {k: mv[k] for k in _PLANNER_META_ALLOWED_KEYS}

    if "global_constraints" in data:
        gv = data["global_constraints"]
        if gv is None:
            pass
        elif not isinstance(gv, dict):
            raise AIServiceError("Planner 输出中 global_constraints 必须是对象或 null。")
        else:
            forbidden_gc = [k for k in gv.keys() if k not in _PLANNER_GLOBAL_REQUIRED_KEYS]
            if forbidden_gc:
                raise AIServiceError(
                    f"Planner 越权写入 global_constraints 字段：{', '.join(forbidden_gc)}。"
                )
            _require_keys(gv, _PLANNER_GLOBAL_REQUIRED_KEYS, label="global_constraints")
            if not isinstance(gv.get("energy_strategy"), str):
                raise AIServiceError("Planner 输出中 global_constraints.energy_strategy 必须是字符串。")
            if not isinstance(gv.get("sonic_world"), list):
                raise AIServiceError("Planner 输出中 global_constraints.sonic_world 必须是数组。")
            if not isinstance(gv.get("avoid"), list):
                raise AIServiceError("Planner 输出中 global_constraints.avoid 必须是数组。")
            patch["global_constraints"] = {k: gv[k] for k in _PLANNER_GLOBAL_REQUIRED_KEYS}

    if "plan" in data:
        pv = data["plan"]
        if pv is None:
            pass
        elif not isinstance(pv, dict):
            raise AIServiceError("Planner 输出中 plan 必须是对象或 null。")
        else:
            forbidden_plan = [k for k in pv.keys() if k not in _PLANNER_PLAN_REQUIRED_KEYS]
            if forbidden_plan:
                raise AIServiceError(f"Planner 越权写入 plan 字段：{', '.join(forbidden_plan)}。")
            _require_keys(pv, _PLANNER_PLAN_REQUIRED_KEYS, label="plan")
            if not isinstance(pv.get("segment_count"), int):
                raise AIServiceError("Planner 输出中 plan.segment_count 必须是 int。")
            if not isinstance(pv.get("episode_direction"), str):
                raise AIServiceError("Planner 输出中 plan.episode_direction 必须是字符串。")
            if not isinstance(pv.get("segments_design"), str):
                raise AIServiceError("Planner 输出中 plan.segments_design 必须是字符串。")
            patch["plan"] = {k: pv[k] for k in _PLANNER_PLAN_REQUIRED_KEYS}

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
                _require_keys(segment, _PLANNER_SEGMENT_REQUIRED_KEYS, label=f"segments[{idx}]")
                for list_key in (
                    "sonic_direction",
                    "lyrical_direction",
                    "anchor_tracks",
                    "reference_material",
                    "sequence_direction",
                ):
                    if not isinstance(segment.get(list_key), list):
                        raise AIServiceError(
                            f"Planner 输出中 segments[{idx}].{list_key} 必须是数组。"
                        )
                sanitized_segments.append(
                    {k: segment[k] for k in _PLANNER_SEGMENT_REQUIRED_KEYS}
                )
            patch["segments"] = sanitized_segments

    forbidden_top = [k for k in data.keys() if k not in {"meta", "global_constraints", "plan", "segments"}]
    if forbidden_top:
        raise AIServiceError(f"Planner 越权写入顶层字段：{', '.join(forbidden_top)}。")

    return patch


class PlannerAgent:
    """Planner Agent：全局结构与段落骨架（v6.1 Schema_Planner_v4）。"""

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
        orchestration_mode: str = "legacy",
        audit_stage: str | None = None,
        audit_revision: int | None = None,
    ) -> PlanState:
        orch = (orchestration_mode or "legacy").strip().lower()
        if orch == "staged":
            messages = build_planner_staged_messages(state, mode)
        else:
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

        if audit_sink is not None and round_iteration is not None:
            audit_sink.write_agent_artifact(
                iteration=round_iteration,
                agent_slug=AUDIT_SLUG_PLANNER,
                mode=mode,
                request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                raw_llm_text=raw,
                parsed_patch=data,
                stage=audit_stage,
                revision=audit_revision,
            )

        patch = sanitize_planner_patch(data)
        next_state = merge_plan_state(state, patch)
        next_state = merge_plan_state(
            next_state,
            {"control": {"last_updated_by": "Planner"}},
        )
        return next_state

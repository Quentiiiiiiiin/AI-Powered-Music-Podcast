from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_response_schemas import (
    CRITIC_RESPONSE_SCHEMA,
    CRITIC_STAGED_RESPONSE_SCHEMA,
    build_openrouter_response_format,
)
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.critic_rules import (
    CRITIC_SCORE_DIMS,
    CRITIC_SEVERITIES,
    assert_planner_revision_constraints,
    derive_critic_pass,
    derive_next_agent,
    normalize_target_agent,
)
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_CRITIC, PlanAuditSink
from podcast_ai.modules.theme.prompts import build_critic_agent_messages
from podcast_ai.modules.theme.prompts_staged import build_critic_staged_messages
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Critic Agent"

# 模型允许写入的 critic 字段（pass/threshold 由系统派生，禁止模型输出）
_CRITIC_MODEL_KEYS = {"overall_score", "scores", "issues", "actions"}
_CRITIC_FORBIDDEN_MODEL_KEYS = frozenset({"pass", "threshold"})
_ISSUE_ALLOWED_KEYS = {
    "type",
    "severity",
    "location",
    "problem",
    "listener_impact",
    "suggestion",
}
_ACTION_ALLOWED_KEYS = {"target_agent", "location", "instruction"}


class CriticAgent:
    """v6.5 Critic：模型只评分数/issues/actions；pass 与 legacy next_agent 由系统规则派生。"""

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
        stage: str | None = None,
        audit_stage: str | None = None,
        audit_revision: int | None = None,
    ) -> PlanState:
        """
        orchestration_mode:
          - legacy：sanitize 后系统写 control.next_agent
          - staged：仅写 critic.*（含派生 pass）；须传 stage
        """
        orch = (orchestration_mode or "legacy").strip().lower()
        if orch == "staged":
            if not stage:
                raise AIServiceError("staged Critic 必须指定 stage（planner|music_curator|script_writer）。")
            messages = build_critic_staged_messages(state, mode, stage=stage)
            schema = CRITIC_STAGED_RESPONSE_SCHEMA
            schema_name = "podcast_critic_staged_response"
        else:
            messages = build_critic_agent_messages(state, mode)
            schema = CRITIC_RESPONSE_SCHEMA
            schema_name = "podcast_critic_response"

        gen_kwargs: Dict[str, Any] = {"temperature": 0.2}
        structured = should_use_structured_output(self._settings.llm)
        if structured:
            gen_kwargs["response_format"] = build_openrouter_response_format(
                schema_name,
                schema,
            )

        try:
            raw = self._llm.generate(messages, **gen_kwargs)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Critic Agent 失败：{exc!r}") from exc

        logger.debug("Critic Agent raw output (truncated): %s", raw[:1000])

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
                agent_slug=AUDIT_SLUG_CRITIC,
                mode=mode,
                request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                raw_llm_text=raw,
                parsed_patch=data,
                stage=audit_stage,
                revision=audit_revision,
            )

        model_body = _sanitize_critic_model_payload(data, staged=(orch == "staged"))

        # v6.5：staged Planner revision 轻量护栏（禁止新 issues；issues 减少则分数不降）
        mode_n = (mode or "").strip().lower()
        if orch == "staged" and stage == "planner" and mode_n == "revision":
            prev_critic = state.get("critic") if isinstance(state.get("critic"), dict) else None
            assert_planner_revision_constraints(prev_critic, model_body)

        passed, thr = derive_critic_pass(model_body)
        critic_out: Dict[str, Any] = {
            **model_body,
            "pass": passed,
            "threshold": thr,
        }
        patch: Dict[str, Any] = {"critic": critic_out}

        if orch == "legacy":
            prev = None
            control = state.get("control")
            if isinstance(control, dict):
                prev = control.get("next_agent") if isinstance(control.get("next_agent"), str) else None
            next_agent = derive_next_agent(
                model_body,
                passed=passed,
                previous_next_agent=prev,
            )
            # pass=true 时不改写 next_agent（编排以 critic.pass 结束）
            if next_agent is not None:
                patch["control"] = {"next_agent": next_agent}

        return merge_plan_state(state, patch)


def _sanitize_critic_model_payload(data: Dict[str, Any], *, staged: bool) -> Dict[str, Any]:
    """校验模型输出；只返回 overall_score/scores/issues/actions（不含 pass/threshold/control）。"""
    if staged:
        allowed_top = {"critic"}
        forbidden_top = set(data.keys()) - allowed_top
        if forbidden_top:
            raise AIServiceError(
                f"staged Critic 禁止写入字段：{', '.join(sorted(forbidden_top))}（含 control.next_agent）。"
            )
    else:
        # legacy 也不再接受模型 control；系统派生 next_agent
        allowed_top = {"critic"}
        forbidden_top = set(data.keys()) - allowed_top
        if forbidden_top:
            raise AIServiceError(f"Critic 越权写入顶层字段：{', '.join(sorted(forbidden_top))}。")

    critic = data.get("critic")
    if not isinstance(critic, dict):
        raise AIServiceError("Critic 输出中 critic 必须是对象。")

    leaked = _CRITIC_FORBIDDEN_MODEL_KEYS.intersection(critic.keys())
    if leaked:
        raise AIServiceError(
            f"Critic 禁止输出系统字段：{', '.join(sorted(leaked))}（由系统规则派生）。"
        )

    forbidden_critic_keys = set(critic.keys()) - _CRITIC_MODEL_KEYS
    if forbidden_critic_keys:
        raise AIServiceError(f"Critic 越权写入 critic 字段：{', '.join(sorted(forbidden_critic_keys))}。")

    missing = [k for k in ("overall_score", "scores", "issues", "actions") if k not in critic]
    if missing:
        raise AIServiceError(f"Critic 输出缺少字段：{', '.join(missing)}。")

    overall = critic.get("overall_score")
    if not isinstance(overall, int) or overall < 0 or overall > 100:
        raise AIServiceError("Critic 输出中 critic.overall_score 必须是 0–100 的 int。")

    scores = critic.get("scores")
    if not isinstance(scores, dict):
        raise AIServiceError("Critic 输出中 critic.scores 必须是对象。")
    forbidden_scores = set(scores.keys()) - set(CRITIC_SCORE_DIMS)
    if forbidden_scores:
        raise AIServiceError(f"Critic 越权写入 critic.scores 字段：{', '.join(sorted(forbidden_scores))}。")
    missing_scores = [k for k in CRITIC_SCORE_DIMS if k not in scores]
    if missing_scores:
        raise AIServiceError(f"Critic 输出中 critic.scores 缺少字段：{', '.join(missing_scores)}。")
    sanitized_scores: Dict[str, int] = {}
    for dim in CRITIC_SCORE_DIMS:
        val = scores[dim]
        if not isinstance(val, int) or val < 0 or val > 100:
            raise AIServiceError(f"Critic 输出中 critic.scores.{dim} 必须是 0–100 的 int。")
        sanitized_scores[dim] = val

    issues = critic.get("issues")
    if not isinstance(issues, list):
        raise AIServiceError("Critic 输出中 critic.issues 必须是数组。")
    sanitized_issues: List[Dict[str, Any]] = []
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise AIServiceError(f"Critic 输出中 critic.issues[{idx}] 必须是对象。")
        forbidden_issue = set(issue.keys()) - _ISSUE_ALLOWED_KEYS
        if forbidden_issue:
            raise AIServiceError(
                f"Critic 越权写入 critic.issues[{idx}] 字段：{', '.join(sorted(forbidden_issue))}。",
            )
        for k in _ISSUE_ALLOWED_KEYS:
            if k not in issue:
                raise AIServiceError(f"Critic 输出中 critic.issues[{idx}] 缺少字段：{k}。")
        sev = issue.get("severity")
        if not isinstance(sev, str) or sev not in CRITIC_SEVERITIES:
            raise AIServiceError(
                f"Critic 输出中 critic.issues[{idx}].severity 必须是 "
                f"{'/'.join(sorted(CRITIC_SEVERITIES))}。"
            )
        for k in _ISSUE_ALLOWED_KEYS - {"severity"}:
            if not isinstance(issue.get(k), str):
                raise AIServiceError(f"Critic 输出中 critic.issues[{idx}].{k} 必须是字符串。")
        sanitized_issues.append({k: issue[k] for k in _ISSUE_ALLOWED_KEYS})

    actions = critic.get("actions")
    if not isinstance(actions, list):
        raise AIServiceError("Critic 输出中 critic.actions 必须是数组。")
    sanitized_actions: List[Dict[str, Any]] = []
    for idx, action in enumerate(actions):
        if not isinstance(action, dict):
            raise AIServiceError(f"Critic 输出中 critic.actions[{idx}] 必须是对象。")
        forbidden_action = set(action.keys()) - _ACTION_ALLOWED_KEYS
        if forbidden_action:
            raise AIServiceError(
                f"Critic 越权写入 critic.actions[{idx}] 字段：{', '.join(sorted(forbidden_action))}。",
            )
        for k in _ACTION_ALLOWED_KEYS:
            if k not in action:
                raise AIServiceError(f"Critic 输出中 critic.actions[{idx}] 缺少字段：{k}。")
            if not isinstance(action.get(k), str):
                raise AIServiceError(f"Critic 输出中 critic.actions[{idx}].{k} 必须是字符串。")
        sanitized_actions.append(
            {
                "target_agent": normalize_target_agent(action["target_agent"]),
                "location": action["location"],
                "instruction": action["instruction"],
            }
        )

    return {
        "overall_score": overall,
        "scores": sanitized_scores,
        "issues": sanitized_issues,
        "actions": sanitized_actions,
    }


def _sanitize_critic_patch_staged(data: Dict[str, Any]) -> Dict[str, Any]:
    """测试/兼容入口：仅返回模型字段 patch（不含系统派生 pass）。"""
    return {"critic": _sanitize_critic_model_payload(data, staged=True)}

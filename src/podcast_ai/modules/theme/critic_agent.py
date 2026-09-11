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
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_CRITIC, PlanAuditSink
from podcast_ai.modules.theme.prompts import build_critic_agent_messages
from podcast_ai.modules.theme.prompts_staged import build_critic_staged_messages
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Critic Agent"


class CriticAgent:
    """v3.0 Critic Agent：结构化评估与修复动作输出。"""

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
          - legacy：写 control.next_agent（旧路径）
          - staged：仅 critic.*，禁止 next_agent；须传 stage
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

        if orch == "staged":
            patch = _sanitize_critic_patch_staged(data)
        else:
            patch = _sanitize_critic_patch(data)
        next_state = merge_plan_state(state, patch)
        return next_state


_CRITIC_ALLOWED_KEYS = {"pass", "scores", "issues", "actions"}
_SCORES_ALLOWED_KEYS = {"coherence", "emotion_flow", "immersion"}
_ISSUE_ALLOWED_KEYS = {"type", "location", "problem", "suggestion"}
_ACTION_ALLOWED_KEYS = {"target_agent", "instruction"}
_CONTROL_ALLOWED_KEYS = {"next_agent"}


def _sanitize_critic_body(critic: Dict[str, Any]) -> Dict[str, Any]:
    """共享 critic.* 校验（legacy / staged）。"""
    forbidden_critic_keys = set(critic.keys()) - _CRITIC_ALLOWED_KEYS
    if forbidden_critic_keys:
        raise AIServiceError(f"Critic 越权写入 critic 字段：{', '.join(sorted(forbidden_critic_keys))}。")

    if "pass" not in critic or "scores" not in critic or "issues" not in critic or "actions" not in critic:
        missing = [k for k in ("pass", "scores", "issues", "actions") if k not in critic]
        raise AIServiceError(f"Critic 输出缺少字段：{', '.join(missing)}。")

    if not isinstance(critic.get("pass"), bool):
        raise AIServiceError("Critic 输出中 critic.pass 必须是 bool。")

    scores = critic.get("scores")
    if not isinstance(scores, dict):
        raise AIServiceError("Critic 输出中 critic.scores 必须是对象。")
    forbidden_scores_keys = set(scores.keys()) - _SCORES_ALLOWED_KEYS
    if forbidden_scores_keys:
        raise AIServiceError(f"Critic 越权写入 critic.scores 字段：{', '.join(sorted(forbidden_scores_keys))}。")
    if not all(k in scores for k in _SCORES_ALLOWED_KEYS):
        missing = [k for k in sorted(_SCORES_ALLOWED_KEYS) if k not in scores]
        raise AIServiceError(f"Critic 输出中 critic.scores 缺少字段：{', '.join(missing)}。")
    for k in _SCORES_ALLOWED_KEYS:
        if not isinstance(scores.get(k), int):
            raise AIServiceError(f"Critic 输出中 critic.scores.{k} 必须是 int。")

    issues = critic.get("issues")
    if not isinstance(issues, list):
        raise AIServiceError("Critic 输出中 critic.issues 必须是数组。")
    sanitized_issues: List[Dict[str, Any]] = []
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise AIServiceError(f"Critic 输出中 critic.issues[{idx}] 必须是对象。")
        forbidden_issue_keys = set(issue.keys()) - _ISSUE_ALLOWED_KEYS
        if forbidden_issue_keys:
            raise AIServiceError(
                f"Critic 越权写入 critic.issues[{idx}] 字段：{', '.join(sorted(forbidden_issue_keys))}。",
            )
        for k in _ISSUE_ALLOWED_KEYS:
            if k not in issue:
                raise AIServiceError(f"Critic 输出中 critic.issues[{idx}] 缺少字段：{k}。")
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
        forbidden_action_keys = set(action.keys()) - _ACTION_ALLOWED_KEYS
        if forbidden_action_keys:
            raise AIServiceError(
                f"Critic 越权写入 critic.actions[{idx}] 字段：{', '.join(sorted(forbidden_action_keys))}。",
            )
        for k in _ACTION_ALLOWED_KEYS:
            if k not in action:
                raise AIServiceError(f"Critic 输出中 critic.actions[{idx}] 缺少字段：{k}。")
            if not isinstance(action.get(k), str):
                raise AIServiceError(f"Critic 输出中 critic.actions[{idx}].{k} 必须是字符串。")
        sanitized_actions.append({k: action[k] for k in _ACTION_ALLOWED_KEYS})

    if critic["pass"] is False and len(sanitized_actions) < 1:
        raise AIServiceError("Critic.pass=false 时，critic.actions 至少包含 1 条修复指令。")

    return {
        "pass": critic["pass"],
        "scores": {k: scores[k] for k in _SCORES_ALLOWED_KEYS},
        "issues": sanitized_issues,
        "actions": sanitized_actions,
    }


def _sanitize_critic_patch(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed_top = {"critic", "control"}
    forbidden_top = set(data.keys()) - allowed_top
    if forbidden_top:
        raise AIServiceError(f"Critic 越权写入顶层字段：{', '.join(sorted(forbidden_top))}。")

    critic = data.get("critic")
    if not isinstance(critic, dict):
        raise AIServiceError("Critic 输出中 critic 必须是对象。")

    body = _sanitize_critic_body(critic)

    control = data.get("control")
    if control is None:
        raise AIServiceError("Critic 输出中 control 必须包含 next_agent。")
    if not isinstance(control, dict):
        raise AIServiceError("Critic 输出中 control 必须是对象。")
    forbidden_control_keys = set(control.keys()) - _CONTROL_ALLOWED_KEYS
    if forbidden_control_keys:
        raise AIServiceError(f"Critic 越权写入 control 字段：{', '.join(sorted(forbidden_control_keys))}。")
    if "next_agent" not in control:
        raise AIServiceError("Critic 输出中 control.next_agent 缺失。")
    if not isinstance(control.get("next_agent"), str):
        raise AIServiceError("Critic 输出中 control.next_agent 必须是字符串。")

    return {
        "critic": body,
        "control": {"next_agent": control["next_agent"]},
    }


def _sanitize_critic_patch_staged(data: Dict[str, Any]) -> Dict[str, Any]:
    """v6.0 staged：仅允许 critic.*；若模型误写 control 则拒绝。"""
    allowed_top = {"critic"}
    forbidden_top = set(data.keys()) - allowed_top
    if forbidden_top:
        raise AIServiceError(
            f"staged Critic 禁止写入字段：{', '.join(sorted(forbidden_top))}（含 control.next_agent）。"
        )

    critic = data.get("critic")
    if not isinstance(critic, dict):
        raise AIServiceError("Critic 输出中 critic 必须是对象。")

    return {"critic": _sanitize_critic_body(critic)}

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_response_schemas import (
    CRITIC_RESPONSE_SCHEMA,
    CRITIC_STAGED_RESPONSE_SCHEMA,
    CURATOR_CRITIC_RESPONSE_SCHEMA,
    build_openrouter_response_format,
)
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.critic_rules import (
    CURATOR_CRITIC_SCORE_DIMS,
    CURATOR_CRITIC_SEVERITIES,
    PLANNER_CRITIC_SCORE_DIMS,
    PLANNER_CRITIC_SEVERITIES,
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

_CRITIC_FORBIDDEN_MODEL_KEYS = frozenset({"pass", "threshold", "overall_score"})

_PLANNER_ISSUE_KEYS = frozenset(
    {"type", "severity", "location", "problem", "listener_impact", "suggestion"}
)
_PLANNER_ACTION_KEYS = frozenset({"target_agent", "location", "instruction"})
_CURATOR_ISSUE_KEYS = frozenset(
    {"type", "severity", "location", "problem", "reason", "suggestion"}
)
# v6.7：与 Planner actions 字段集对齐
_CURATOR_ACTION_KEYS = frozenset({"target_agent", "location", "instruction"})


def _staged_critic_profile(stage: str) -> dict[str, Any]:
    """按阶段选择 schema / 分数维 / severity（扩展点：未来可加 Writer）。"""
    key = (stage or "").strip().lower()
    if key == "music_curator":
        return {
            "schema": CURATOR_CRITIC_RESPONSE_SCHEMA,
            "schema_name": "podcast_curator_critic_staged_response",
            "score_dims": CURATOR_CRITIC_SCORE_DIMS,
            "severities": CURATOR_CRITIC_SEVERITIES,
            "issue_keys": _CURATOR_ISSUE_KEYS,
            "action_keys": _CURATOR_ACTION_KEYS,
        }
    # planner / script_writer：暂用 Planner Critic 契约（Writer 专用 Guide 本轮不做）
    return {
        "schema": CRITIC_STAGED_RESPONSE_SCHEMA,
        "schema_name": "podcast_critic_staged_response",
        "score_dims": PLANNER_CRITIC_SCORE_DIMS,
        "severities": PLANNER_CRITIC_SEVERITIES,
        "issue_keys": _PLANNER_ISSUE_KEYS,
        "action_keys": _PLANNER_ACTION_KEYS,
    }


class CriticAgent:
    """v6.7 Critic：按阶段切换 schema/sanitize/pass 维；无 overall_score；pass 由系统派生。"""

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
          - legacy：Planner Critic schema；系统写 control.next_agent
          - staged：按 stage 切换 schema/prompt/分数维；仅写 critic.*
        """
        orch = (orchestration_mode or "legacy").strip().lower()
        if orch == "staged":
            if not stage:
                raise AIServiceError("staged Critic 必须指定 stage（planner|music_curator|script_writer）。")
            profile = _staged_critic_profile(stage)
            messages = build_critic_staged_messages(state, mode, stage=stage)
            schema = profile["schema"]
            schema_name = profile["schema_name"]
        else:
            # legacy：继续用 Planner Critic（本轮不强制切 Curator 专用）
            profile = {
                "schema": CRITIC_RESPONSE_SCHEMA,
                "schema_name": "podcast_critic_response",
                "score_dims": PLANNER_CRITIC_SCORE_DIMS,
                "severities": PLANNER_CRITIC_SEVERITIES,
                "issue_keys": _PLANNER_ISSUE_KEYS,
                "action_keys": _PLANNER_ACTION_KEYS,
            }
            messages = build_critic_agent_messages(state, mode)
            schema = profile["schema"]
            schema_name = profile["schema_name"]

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

        # v7.1：解析失败仍落盘原始返回（parsed_patch=None）；写盘失败不掩盖解析错误
        try:
            data = parse_agent_json_response(
                agent_label=AGENT_LABEL,
                raw=raw,
                state=state,
                structured=structured,
                allow_repair_fallback=not structured,
                output_dir=Path(self._settings.app.output_dir),
            )
        except Exception:
            if audit_sink is not None and round_iteration is not None:
                audit_sink.write_agent_artifact(
                    iteration=round_iteration,
                    agent_slug=AUDIT_SLUG_CRITIC,
                    mode=mode,
                    request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                    raw_llm_text=raw,
                    parsed_patch=None,
                    stage=audit_stage,
                    revision=audit_revision,
                )
            raise

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

        model_body = _sanitize_critic_model_payload(
            data,
            staged=(orch == "staged"),
            score_dims=profile["score_dims"],
            severities=profile["severities"],
            issue_keys=profile["issue_keys"],
            action_keys=profile["action_keys"],
        )

        # v6.8：不再对 revision「新 issue / 分数不降」做系统硬护栏（prompt 软引导仍保留）
        passed, thr = derive_critic_pass(
            model_body,
            score_dims=profile["score_dims"],
            allowed_severities=profile["severities"],
        )
        critic_out: Dict[str, Any] = {
            **model_body,
            "pass": passed,
            "threshold": thr,
        }

        # 整块替换 critic，避免 Planner/Curator 分数维在 merge 时互相污染
        next_state = merge_plan_state(state, {})
        next_state["critic"] = critic_out

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
            if next_agent is not None:
                next_state = merge_plan_state(next_state, {"control": {"next_agent": next_agent}})

        return next_state


def _sanitize_critic_model_payload(
    data: Dict[str, Any],
    *,
    staged: bool,
    score_dims: Sequence[str],
    severities: frozenset[str],
    issue_keys: frozenset[str],
    action_keys: frozenset[str],
) -> Dict[str, Any]:
    """校验模型输出；只返回 scores/issues/actions（不含 pass/threshold/overall_score/control）。"""
    allowed_top = {"critic"}
    forbidden_top = set(data.keys()) - allowed_top
    if forbidden_top:
        prefix = "staged Critic 禁止写入字段" if staged else "Critic 越权写入顶层字段"
        raise AIServiceError(f"{prefix}：{', '.join(sorted(forbidden_top))}。")

    critic = data.get("critic")
    if not isinstance(critic, dict):
        raise AIServiceError("Critic 输出中 critic 必须是对象。")

    leaked = _CRITIC_FORBIDDEN_MODEL_KEYS.intersection(critic.keys())
    if leaked:
        raise AIServiceError(
            f"Critic 禁止输出字段：{', '.join(sorted(leaked))}（pass/threshold 由系统派生；overall_score 已移除）。"
        )

    model_keys = {"scores", "issues", "actions"}
    forbidden_critic_keys = set(critic.keys()) - model_keys
    if forbidden_critic_keys:
        raise AIServiceError(f"Critic 越权写入 critic 字段：{', '.join(sorted(forbidden_critic_keys))}。")

    missing = [k for k in ("scores", "issues", "actions") if k not in critic]
    if missing:
        raise AIServiceError(f"Critic 输出缺少字段：{', '.join(missing)}。")

    out: Dict[str, Any] = {}

    scores = critic.get("scores")
    if not isinstance(scores, dict):
        raise AIServiceError("Critic 输出中 critic.scores 必须是对象。")
    forbidden_scores = set(scores.keys()) - set(score_dims)
    if forbidden_scores:
        raise AIServiceError(f"Critic 越权写入 critic.scores 字段：{', '.join(sorted(forbidden_scores))}。")
    missing_scores = [k for k in score_dims if k not in scores]
    if missing_scores:
        raise AIServiceError(f"Critic 输出中 critic.scores 缺少字段：{', '.join(missing_scores)}。")
    sanitized_scores: Dict[str, int] = {}
    for dim in score_dims:
        val = scores[dim]
        if not isinstance(val, int) or val < 0 or val > 100:
            raise AIServiceError(f"Critic 输出中 critic.scores.{dim} 必须是 0–100 的 int。")
        sanitized_scores[dim] = val
    out["scores"] = sanitized_scores

    issues = critic.get("issues")
    if not isinstance(issues, list):
        raise AIServiceError("Critic 输出中 critic.issues 必须是数组。")
    sanitized_issues: List[Dict[str, Any]] = []
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise AIServiceError(f"Critic 输出中 critic.issues[{idx}] 必须是对象。")
        forbidden_issue = set(issue.keys()) - issue_keys
        if forbidden_issue:
            raise AIServiceError(
                f"Critic 越权写入 critic.issues[{idx}] 字段：{', '.join(sorted(forbidden_issue))}。",
            )
        for k in issue_keys:
            if k not in issue:
                raise AIServiceError(f"Critic 输出中 critic.issues[{idx}] 缺少字段：{k}。")
        sev = issue.get("severity")
        if not isinstance(sev, str) or sev not in severities:
            raise AIServiceError(
                f"Critic 输出中 critic.issues[{idx}].severity 必须是 "
                f"{'/'.join(sorted(severities))}。"
            )
        for k in issue_keys - {"severity"}:
            if not isinstance(issue.get(k), str):
                raise AIServiceError(f"Critic 输出中 critic.issues[{idx}].{k} 必须是字符串。")
        sanitized_issues.append({k: issue[k] for k in issue_keys})
    out["issues"] = sanitized_issues

    actions = critic.get("actions")
    if not isinstance(actions, list):
        raise AIServiceError("Critic 输出中 critic.actions 必须是数组。")
    sanitized_actions: List[Dict[str, Any]] = []
    for idx, action in enumerate(actions):
        if not isinstance(action, dict):
            raise AIServiceError(f"Critic 输出中 critic.actions[{idx}] 必须是对象。")
        forbidden_action = set(action.keys()) - action_keys
        if forbidden_action:
            raise AIServiceError(
                f"Critic 越权写入 critic.actions[{idx}] 字段：{', '.join(sorted(forbidden_action))}。",
            )
        for k in action_keys:
            if k not in action:
                raise AIServiceError(f"Critic 输出中 critic.actions[{idx}] 缺少字段：{k}。")
            if not isinstance(action.get(k), str):
                raise AIServiceError(f"Critic 输出中 critic.actions[{idx}].{k} 必须是字符串。")
        item = {k: action[k] for k in action_keys}
        item["target_agent"] = normalize_target_agent(action["target_agent"])
        sanitized_actions.append(item)
    out["actions"] = sanitized_actions
    return out


def _sanitize_critic_patch_staged(data: Dict[str, Any]) -> Dict[str, Any]:
    """测试兼容：默认按 Planner Critic 契约 sanitize。"""
    return {
        "critic": _sanitize_critic_model_payload(
            data,
            staged=True,
            score_dims=PLANNER_CRITIC_SCORE_DIMS,
            severities=PLANNER_CRITIC_SEVERITIES,
            issue_keys=_PLANNER_ISSUE_KEYS,
            action_keys=_PLANNER_ACTION_KEYS,
        )
    }


def _sanitize_curator_critic_patch_staged(data: Dict[str, Any]) -> Dict[str, Any]:
    """测试入口：Curator Critic sanitize。"""
    return {
        "critic": _sanitize_critic_model_payload(
            data,
            staged=True,
            score_dims=CURATOR_CRITIC_SCORE_DIMS,
            severities=CURATOR_CRITIC_SEVERITIES,
            issue_keys=_CURATOR_ISSUE_KEYS,
            action_keys=_CURATOR_ACTION_KEYS,
        )
    }

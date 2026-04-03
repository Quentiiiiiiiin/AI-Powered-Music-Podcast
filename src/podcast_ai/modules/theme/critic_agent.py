from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_response_schemas import (
    CRITIC_RESPONSE_SCHEMA,
    build_openrouter_response_format,
)
from podcast_ai.modules.theme.json_repair import dump_json_repair_debug, repair_and_standardize_json
from podcast_ai.modules.theme.prompts import build_critic_agent_messages
from podcast_ai.modules.theme.state import PlanState, assert_plan_state_valid, merge_plan_state

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

    def run(self, state: PlanState) -> PlanState:
        assert_plan_state_valid(state)

        messages = build_critic_agent_messages(state)

        gen_kwargs: Dict[str, Any] = {"temperature": 0.2}
        structured = should_use_structured_output(self._settings.llm)
        if structured:
            gen_kwargs["response_format"] = build_openrouter_response_format(
                "podcast_critic_response",
                CRITIC_RESPONSE_SCHEMA,
            )

        try:
            raw = self._llm.generate(messages, **gen_kwargs)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Critic Agent 失败：{exc!r}") from exc

        logger.debug("Critic Agent raw output (truncated): %s", raw[:1000])

        try:
            repaired = repair_and_standardize_json(raw)
            data = json.loads(repaired)
        except Exception as exc:  # noqa: BLE001
            dump_info = dump_json_repair_debug(
                output_dir=Path(self._settings.app.output_dir),
                agent_name="Critic",
                state=state,
                raw=raw,
                repaired=locals().get("repaired", ""),
                exc=exc,
            )
            req_id = str((state.get("meta") or {}).get("request_id") or "unknown")
            iter_s = str((state.get("control") or {}).get("iteration") or "na")
            so_note = "；已启用 OpenRouter 结构化输出仍解析失败" if structured else ""
            logger.error(
                "%s json.loads 失败 request_id=%s iteration=%s：%s；debug=%s%s",
                AGENT_LABEL,
                req_id,
                iter_s,
                str(exc),
                dump_info.dump_path,
                so_note,
            )
            raise AIServiceError(
                f"{AGENT_LABEL} 返回结果不是有效 JSON（request_id={req_id}, iteration={iter_s}{so_note}；已保存：{dump_info.dump_path}）。"
            ) from exc
        if not isinstance(data, dict):
            raise AIServiceError("Critic Agent 返回的 JSON 顶层必须是对象。")

        patch = _sanitize_critic_patch(data)
        next_state = merge_plan_state(state, patch)
        assert_plan_state_valid(next_state)
        return next_state


_CRITIC_ALLOWED_KEYS = {"pass", "scores", "issues", "actions"}
_SCORES_ALLOWED_KEYS = {"coherence", "emotion_flow", "immersion"}
_ISSUE_ALLOWED_KEYS = {"type", "location", "problem", "suggestion"}
_ACTION_ALLOWED_KEYS = {"target_agent", "instruction"}
_CONTROL_ALLOWED_KEYS = {"next_agent"}


def _sanitize_critic_patch(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed_top = {"critic", "control"}
    forbidden_top = set(data.keys()) - allowed_top
    if forbidden_top:
        raise AIServiceError(f"Critic 越权写入顶层字段：{', '.join(sorted(forbidden_top))}。")

    critic = data.get("critic")
    if not isinstance(critic, dict):
        raise AIServiceError("Critic 输出中 critic 必须是对象。")

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
        "critic": {
            "pass": critic["pass"],
            "scores": {k: scores[k] for k in _SCORES_ALLOWED_KEYS},
            "issues": sanitized_issues,
            "actions": sanitized_actions,
        },
        "control": {"next_agent": control["next_agent"]},
    }

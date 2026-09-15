"""v6.5：Critic Agent sanitize + 系统派生 + Planner revision 护栏。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.critic_rules import CRITIC_SCORE_DIMS
from podcast_ai.modules.theme.state import initialize_plan_state


class _StubLLMClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def _request() -> EpisodeRequest:
    return EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language="zh",
        output_dir=Path("./output"),
    )


def _scores(value: int) -> dict[str, int]:
    return {dim: value for dim in CRITIC_SCORE_DIMS}


def _model_critic(
    *,
    score: int,
    overall_score: int = 70,
    issues: list | None = None,
    actions: list | None = None,
) -> dict[str, Any]:
    return {
        "overall_score": overall_score,
        "scores": _scores(score),
        "issues": issues if issues is not None else [],
        "actions": actions if actions is not None else [],
    }


def test_v65_critic_fail_derives_pass_false_and_next_agent() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "critic": _model_critic(
            score=50,
            issues=[
                {
                    "type": "sequence_narrative",
                    "severity": "critical",
                    "location": "segments[0].playlist[0]",
                    "problem": "情绪跳跃过大",
                    "listener_impact": "断档",
                    "suggestion": "换曲",
                }
            ],
            actions=[
                {
                    "target_agent": "Music Curator",
                    "location": "segments[0].playlist[0]",
                    "instruction": "调整 playlist 情绪过渡。",
                }
            ],
        )
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)
    assert next_state["critic"]["pass"] is False
    assert next_state["critic"]["threshold"]["theme_definition"] == 80
    assert len(next_state["critic"]["actions"]) == 1
    assert next_state["control"]["next_agent"] == "Music Curator"


def test_v65_critic_pass_true_at_81() -> None:
    state = initialize_plan_state(_request())
    state["control"]["next_agent"] = "Script Writer"
    payload = {"critic": _model_critic(score=81, issues=[], actions=[])}
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)
    assert next_state["critic"]["pass"] is True
    assert next_state["control"]["next_agent"] == "Script Writer"


def test_v65_critic_score_80_is_not_pass() -> None:
    state = initialize_plan_state(_request())
    payload = {"critic": _model_critic(score=80, issues=[], actions=[])}
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)
    assert next_state["critic"]["pass"] is False


def test_v65_critic_rejects_model_pass() -> None:
    state = initialize_plan_state(_request())
    body = _model_critic(score=81)
    body["pass"] = True
    agent = CriticAgent(llm_client=_StubLLMClient({"critic": body}))
    with pytest.raises(AIServiceError, match=r"禁止输出系统字段|pass"):
        agent.run(state)


def test_v65_critic_rejects_model_control() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "critic": _model_critic(score=81),
        "control": {"next_agent": "Planner"},
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    with pytest.raises(AIServiceError, match=r"越权写入顶层字段"):
        agent.run(state)


def test_v65_staged_does_not_write_next_agent() -> None:
    state = initialize_plan_state(_request())
    state["control"]["next_agent"] = "Planner"
    payload = {
        "critic": _model_critic(
            score=40,
            actions=[
                {
                    "target_agent": "Music Curator",
                    "location": "segments[0]",
                    "instruction": "fix playlist",
                }
            ],
        )
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state, orchestration_mode="staged", stage="music_curator")
    assert next_state["critic"]["pass"] is False
    assert next_state["control"]["next_agent"] == "Planner"


def test_v65_planner_revision_rejects_new_issues() -> None:
    state = initialize_plan_state(_request())
    state["critic"]["scores"] = _scores(60)
    state["critic"]["issues"] = [
        {
            "type": "x",
            "severity": "critical",
            "location": "plan",
            "problem": "old",
            "listener_impact": "i",
            "suggestion": "s",
        }
    ]
    payload = {
        "critic": _model_critic(
            score=65,
            issues=[
                {
                    "type": "x",
                    "severity": "critical",
                    "location": "plan",
                    "problem": "brand new issue",
                    "listener_impact": "i",
                    "suggestion": "s",
                }
            ],
            actions=[],
        )
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    with pytest.raises(AIServiceError, match="禁止新增 issues"):
        agent.run(state, mode="revision", orchestration_mode="staged", stage="planner")

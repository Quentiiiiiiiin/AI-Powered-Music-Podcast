"""v6.4：Critic 系统规则（pass / next_agent）单测。"""
from __future__ import annotations

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.modules.theme.critic_rules import (
    CRITIC_SCORE_DIMS,
    DEFAULT_CRITIC_THRESHOLDS,
    derive_critic_pass,
    derive_next_agent,
)


def _scores(value: int) -> dict[str, int]:
    return {dim: value for dim in CRITIC_SCORE_DIMS}


def _body(
    *,
    score: int = 7,
    issues: list | None = None,
    actions: list | None = None,
    overall_score: int = 70,
) -> dict:
    return {
        "overall_score": overall_score,
        "scores": _scores(score),
        "issues": issues if issues is not None else [],
        "actions": actions if actions is not None else [],
    }


def test_pass_when_all_dims_above_threshold_and_empty_actions() -> None:
    passed, thr = derive_critic_pass(_body(score=7, issues=[], actions=[]))
    assert passed is True
    assert thr == DEFAULT_CRITIC_THRESHOLDS


def test_fail_when_any_dim_not_strictly_above_threshold() -> None:
    # threshold 默认 6 → 恰好 6 不过线
    body = _body(score=7)
    body["scores"]["theme_definition"] = 6
    passed, _ = derive_critic_pass(body)
    assert passed is False


def test_fail_when_critical_issue_and_actions_present() -> None:
    body = _body(
        score=8,
        issues=[
            {
                "type": "x",
                "severity": "critical",
                "location": "plan",
                "problem": "p",
                "listener_impact": "i",
                "suggestion": "s",
            }
        ],
        actions=[{"target_agent": "Planner", "location": "plan", "instruction": "fix"}],
    )
    passed, _ = derive_critic_pass(body)
    assert passed is False


def test_pass_when_critical_issue_but_actions_empty() -> None:
    body = _body(
        score=8,
        issues=[
            {
                "type": "x",
                "severity": "critical",
                "location": "plan",
                "problem": "p",
                "listener_impact": "i",
                "suggestion": "s",
            }
        ],
        actions=[],
    )
    passed, _ = derive_critic_pass(body)
    assert passed is True


def test_pass_when_only_minor_issues_with_actions() -> None:
    body = _body(
        score=8,
        issues=[
            {
                "type": "x",
                "severity": "minor",
                "location": "plan",
                "problem": "p",
                "listener_impact": "i",
                "suggestion": "s",
            }
        ],
        actions=[{"target_agent": "Planner", "location": "plan", "instruction": "tweak"}],
    )
    passed, _ = derive_critic_pass(body)
    assert passed is True


def test_unknown_severity_raises() -> None:
    body = _body(
        score=8,
        issues=[
            {
                "type": "x",
                "severity": "major",
                "location": "plan",
                "problem": "p",
                "listener_impact": "i",
                "suggestion": "s",
            }
        ],
    )
    with pytest.raises(AIServiceError, match="severity"):
        derive_critic_pass(body)


def test_missing_score_dim_raises() -> None:
    body = _body(score=8)
    del body["scores"]["creative_freedom"]
    with pytest.raises(AIServiceError, match="creative_freedom"):
        derive_critic_pass(body)


def test_next_agent_priority_planner_first() -> None:
    body = _body(
        actions=[
            {"target_agent": "Script Writer", "location": "s", "instruction": "a"},
            {"target_agent": "Music Curator", "location": "m", "instruction": "b"},
            {"target_agent": "Planner", "location": "p", "instruction": "c"},
        ]
    )
    assert derive_next_agent(body, passed=False) == "Planner"


def test_next_agent_priority_curator_before_writer() -> None:
    body = _body(
        actions=[
            {"target_agent": "script writer", "location": "s", "instruction": "a"},
            {"target_agent": "curator", "location": "m", "instruction": "b"},
        ]
    )
    assert derive_next_agent(body, passed=False) == "Music Curator"


def test_next_agent_none_when_passed() -> None:
    assert derive_next_agent(_body(actions=[]), passed=True) is None


def test_next_agent_fallback_when_fail_without_actions() -> None:
    assert derive_next_agent(_body(actions=[]), passed=False, previous_next_agent="Music Curator") == (
        "Music Curator"
    )
    assert derive_next_agent(_body(actions=[]), passed=False, previous_next_agent=None) == "Planner"

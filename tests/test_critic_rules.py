"""v6.5：Critic 系统规则（pass / next_agent / planner revision 护栏）。"""
from __future__ import annotations

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.modules.theme.critic_rules import (
    CURATOR_CRITIC_SCORE_DIMS,
    CRITIC_SCORE_DIMS,
    DEFAULT_CRITIC_THRESHOLDS,
    assert_planner_revision_constraints,
    derive_critic_pass,
    derive_next_agent,
)


def _scores(value: int) -> dict[str, int]:
    return {dim: value for dim in CRITIC_SCORE_DIMS}


def _issue(*, location: str = "plan", problem: str = "p") -> dict:
    return {
        "type": "x",
        "severity": "critical",
        "location": location,
        "problem": problem,
        "listener_impact": "i",
        "suggestion": "s",
    }


def _body(
    *,
    score: int = 81,
    issues: list | None = None,
    actions: list | None = None,
) -> dict:
    return {
        "scores": _scores(score),
        "issues": issues if issues is not None else [],
        "actions": actions if actions is not None else [],
    }


def test_threshold_default_is_80() -> None:
    assert all(v == 80 for v in DEFAULT_CRITIC_THRESHOLDS.values())


def test_pass_when_all_dims_above_threshold_and_empty_actions() -> None:
    passed, thr = derive_critic_pass(_body(score=81, issues=[], actions=[]))
    assert passed is True
    assert thr == DEFAULT_CRITIC_THRESHOLDS


def test_fail_when_score_equals_threshold() -> None:
    # 严格大于：80 不过线
    passed, _ = derive_critic_pass(_body(score=80))
    assert passed is False


def test_fail_when_any_dim_not_strictly_above_threshold() -> None:
    body = _body(score=81)
    body["scores"]["theme_definition"] = 80
    passed, _ = derive_critic_pass(body)
    assert passed is False


def test_fail_when_critical_issue_and_actions_present() -> None:
    body = _body(
        score=85,
        issues=[_issue()],
        actions=[{"target_agent": "Planner", "location": "plan", "instruction": "fix"}],
    )
    passed, _ = derive_critic_pass(body)
    assert passed is False


def test_pass_when_critical_issue_but_actions_empty() -> None:
    body = _body(score=85, issues=[_issue()], actions=[])
    passed, _ = derive_critic_pass(body)
    assert passed is True


def test_pass_when_only_minor_issues_with_actions() -> None:
    issue = _issue()
    issue["severity"] = "minor"
    body = _body(
        score=85,
        issues=[issue],
        actions=[{"target_agent": "Planner", "location": "plan", "instruction": "tweak"}],
    )
    passed, _ = derive_critic_pass(body)
    assert passed is True


def test_unknown_severity_raises() -> None:
    issue = _issue()
    issue["severity"] = "major"
    with pytest.raises(AIServiceError, match="severity"):
        derive_critic_pass(_body(score=85, issues=[issue]))


def test_missing_score_dim_raises() -> None:
    body = _body(score=85)
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


def test_planner_revision_rejects_new_issue_fingerprint() -> None:
    prev = {"scores": _scores(70), "issues": [_issue(location="plan", problem="old")]}
    new = _body(score=75, issues=[_issue(location="plan", problem="brand new")])
    with pytest.raises(AIServiceError, match="禁止新增 issues"):
        assert_planner_revision_constraints(prev, new)


def test_planner_revision_rejects_score_drop_when_issues_shrink() -> None:
    prev = {
        "scores": _scores(70),
        "issues": [_issue(location="a", problem="1"), _issue(location="b", problem="2")],
    }
    new = _body(score=65, issues=[_issue(location="a", problem="1")])
    with pytest.raises(AIServiceError, match="不得低于上一轮"):
        assert_planner_revision_constraints(prev, new)


def test_planner_revision_allows_score_hold_when_issues_shrink() -> None:
    prev = {
        "scores": _scores(70),
        "issues": [_issue(location="a", problem="1"), _issue(location="b", problem="2")],
    }
    new = _body(score=70, issues=[_issue(location="a", problem="1")])
    assert_planner_revision_constraints(prev, new)  # no raise

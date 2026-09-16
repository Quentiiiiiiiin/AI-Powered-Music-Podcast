"""v6.6：Curator Critic 维度隔离、pass 规则、Guide、终态 state 精简。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.storage.paths import save_state_json
from podcast_ai.modules.theme.critic_agent import (
    CriticAgent,
    _sanitize_curator_critic_patch_staged,
)
from podcast_ai.modules.theme.critic_rules import (
    CURATOR_CRITIC_SCORE_DIMS,
    PLANNER_CRITIC_SCORE_DIMS,
    derive_critic_pass,
)
from podcast_ai.modules.theme.prompts_staged import (
    _GUIDE_CURATOR_CRITIC,
    _GUIDES_DIR,
    _load_guide,
    build_critic_staged_messages,
)
from podcast_ai.modules.theme.state import initialize_plan_state


def _curator_scores(value: int) -> dict[str, int]:
    return {dim: value for dim in CURATOR_CRITIC_SCORE_DIMS}


def _curator_body(*, score: int = 81, issues: list | None = None, actions: list | None = None) -> dict:
    return {
        "scores": _curator_scores(score),
        "issues": issues if issues is not None else [],
        "actions": actions if actions is not None else [],
    }


def test_curator_guide_file_exists() -> None:
    path = _GUIDES_DIR / _GUIDE_CURATOR_CRITIC
    assert path.is_file()
    text = _load_guide(_GUIDE_CURATOR_CRITIC)
    assert "CURATOR CRITIC" in text or "Music Curator" in text
    assert len(text) > 3000


def test_curator_critic_messages_include_guide() -> None:
    state = {
        "meta": {"theme": "x"},
        "critic": {"scores": {}, "issues": [], "actions": []},
        "control": {},
        "plan": {},
        "segments": [],
        "global_constraints": {},
    }
    msgs = build_critic_staged_messages(state, "generation", stage="music_curator")
    sys = msgs[0]["content"]
    assert "PROMPT_Guide_Curator_Critic.txt" in sys
    assert "MODE: GENERATION" in sys
    assert "planner_alignment" in sys
    assert "FORBIDDEN" in sys
    assert "critic.pass" in sys
    assert "PROMPT_Guide_Planner_Critic" not in sys
    assert len(sys) > 4000


def test_planner_critic_messages_unchanged_guide() -> None:
    state = {
        "meta": {"theme": "x"},
        "critic": {"scores": {}, "issues": [], "actions": []},
        "control": {},
        "plan": {},
        "segments": [],
        "global_constraints": {},
    }
    msgs = build_critic_staged_messages(state, "generation", stage="planner")
    sys = msgs[0]["content"]
    assert "PROMPT_Guide_Planner_Critic.txt" in sys
    assert "You are the PLANNER CRITIC agent" in sys
    assert "PROMPT_Guide_Curator_Critic" not in sys


def test_curator_sanitize_rejects_planner_score_dims() -> None:
    payload = {
        "critic": {
            "scores": {dim: 81 for dim in PLANNER_CRITIC_SCORE_DIMS},
            "issues": [],
            "actions": [],
        }
    }
    with pytest.raises(AIServiceError, match=r"缺少字段|越权写入 critic.scores"):
        _sanitize_curator_critic_patch_staged(payload)


def test_curator_sanitize_accepts_curator_dims() -> None:
    patch = _sanitize_curator_critic_patch_staged({"critic": _curator_body(score=82)})
    assert set(patch["critic"]["scores"].keys()) == set(CURATOR_CRITIC_SCORE_DIMS)
    assert "overall_score" not in patch["critic"]


def test_curator_pass_when_dims_above_80_and_empty_actions() -> None:
    passed, thr = derive_critic_pass(
        _curator_body(score=81),
        score_dims=CURATOR_CRITIC_SCORE_DIMS,
    )
    assert passed is True
    assert all(v == 80 for v in thr.values())
    assert set(thr.keys()) == set(CURATOR_CRITIC_SCORE_DIMS)


def test_curator_fail_at_exactly_80() -> None:
    passed, _ = derive_critic_pass(
        _curator_body(score=80),
        score_dims=CURATOR_CRITIC_SCORE_DIMS,
    )
    assert passed is False


def test_curator_major_issue_with_actions_fails() -> None:
    body = _curator_body(
        score=90,
        issues=[
            {
                "type": "track_fitness",
                "severity": "major",
                "location": "segments[0].playlist[0]",
                "problem": "weak fit",
                "reason": "listener drift",
                "suggestion": "replace",
            }
        ],
        actions=[{"target_agent": "Music Curator", "location": "segments[0].playlist[0]", "instruction": "replace track"}],
    )
    passed, _ = derive_critic_pass(
        body,
        score_dims=CURATOR_CRITIC_SCORE_DIMS,
        allowed_severities=frozenset({"minor", "major", "critical"}),
    )
    assert passed is False


def test_curator_minor_only_with_actions_passes() -> None:
    body = _curator_body(
        score=90,
        issues=[
            {
                "type": "track_fitness",
                "severity": "minor",
                "location": "segments[0].playlist[0]",
                "problem": "slightly weak",
                "reason": "minor texture",
                "suggestion": "optional tweak",
            }
        ],
        actions=[{"target_agent": "Music Curator", "location": "segments[0].playlist[0]", "instruction": "optional tweak"}],
    )
    passed, _ = derive_critic_pass(
        body,
        score_dims=CURATOR_CRITIC_SCORE_DIMS,
        allowed_severities=frozenset({"minor", "major", "critical"}),
    )
    assert passed is True


class _StubLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def generate(self, messages, **kwargs):  # noqa: ANN001, ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def test_staged_curator_agent_derives_pass_and_replaces_scores() -> None:
    state = initialize_plan_state(
        EpisodeRequest(topic="t", duration_minutes=30, language="zh", output_dir=Path("./o"))
    )
    # 先模拟 Planner critic 残留维
    assert "theme_definition" in state["critic"]["scores"]
    payload = {"critic": _curator_body(score=85)}
    next_state = CriticAgent(llm_client=_StubLLM(payload)).run(
        state,
        orchestration_mode="staged",
        stage="music_curator",
    )
    assert next_state["critic"]["pass"] is True
    assert set(next_state["critic"]["scores"].keys()) == set(CURATOR_CRITIC_SCORE_DIMS)
    assert "theme_definition" not in next_state["critic"]["scores"]


def test_save_state_json_strips_critic_and_control(tmp_path: Path) -> None:
    state = initialize_plan_state(
        EpisodeRequest(topic="t", duration_minutes=30, language="zh", output_dir=tmp_path)
    )
    assert "critic" in state and "control" in state
    path = save_state_json(state, tmp_path, "ep_test_v66")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "critic" not in raw
    assert "control" not in raw
    assert "meta" in raw and "segments" in raw
    # 内存态未改
    assert "critic" in state and "control" in state

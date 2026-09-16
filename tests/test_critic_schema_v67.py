"""v6.7：Critic schema 一致性——无 overall_score；Curator action.location 必填。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.storage.paths import save_state_json
from podcast_ai.modules.theme.agent_response_schemas import (
    CRITIC_RESPONSE_SCHEMA,
    CURATOR_CRITIC_RESPONSE_SCHEMA,
)
from podcast_ai.modules.theme.critic_agent import (
    CriticAgent,
    _sanitize_critic_patch_staged,
    _sanitize_curator_critic_patch_staged,
)
from podcast_ai.modules.theme.critic_rules import (
    CURATOR_CRITIC_SCORE_DIMS,
    PLANNER_CRITIC_SCORE_DIMS,
    derive_critic_pass,
)
from podcast_ai.modules.theme.state import initialize_plan_state


def _planner_scores(v: int = 81) -> dict[str, int]:
    return {d: v for d in PLANNER_CRITIC_SCORE_DIMS}


def _curator_scores(v: int = 81) -> dict[str, int]:
    return {d: v for d in CURATOR_CRITIC_SCORE_DIMS}


def test_planner_schema_has_no_overall_score() -> None:
    body = CRITIC_RESPONSE_SCHEMA["properties"]["critic"]
    assert "overall_score" not in body["properties"]
    assert "overall_score" not in body["required"]
    assert set(body["required"]) == {"scores", "issues", "actions"}


def test_planner_sanitize_rejects_overall_score() -> None:
    with pytest.raises(AIServiceError, match="overall_score"):
        _sanitize_critic_patch_staged(
            {
                "critic": {
                    "overall_score": 90,
                    "scores": _planner_scores(),
                    "issues": [],
                    "actions": [],
                }
            }
        )


def test_curator_schema_requires_action_location() -> None:
    action = CURATOR_CRITIC_RESPONSE_SCHEMA["properties"]["critic"]["properties"]["actions"]["items"]
    assert "location" in action["properties"]
    assert "location" in action["required"]
    assert set(action["required"]) == {"target_agent", "location", "instruction"}


def test_curator_sanitize_rejects_missing_action_location() -> None:
    with pytest.raises(AIServiceError, match=r"actions\[0\].*location"):
        _sanitize_curator_critic_patch_staged(
            {
                "critic": {
                    "scores": _curator_scores(),
                    "issues": [],
                    "actions": [
                        {"target_agent": "Music Curator", "instruction": "fix track"}
                    ],
                }
            }
        )


def test_curator_sanitize_accepts_action_with_location() -> None:
    patch = _sanitize_curator_critic_patch_staged(
        {
            "critic": {
                "scores": _curator_scores(85),
                "issues": [],
                "actions": [
                    {
                        "target_agent": "Music Curator",
                        "location": "segments[0].playlist[1]",
                        "instruction": "replace track",
                    }
                ],
            }
        }
    )
    assert patch["critic"]["actions"][0]["location"] == "segments[0].playlist[1]"


def test_pass_threshold_80_unchanged_planner() -> None:
    assert derive_critic_pass({"scores": _planner_scores(81), "issues": [], "actions": []})[0] is True
    assert derive_critic_pass({"scores": _planner_scores(80), "issues": [], "actions": []})[0] is False


def test_pass_threshold_80_unchanged_curator() -> None:
    assert (
        derive_critic_pass(
            {"scores": _curator_scores(81), "issues": [], "actions": []},
            score_dims=CURATOR_CRITIC_SCORE_DIMS,
        )[0]
        is True
    )
    assert (
        derive_critic_pass(
            {"scores": _curator_scores(80), "issues": [], "actions": []},
            score_dims=CURATOR_CRITIC_SCORE_DIMS,
        )[0]
        is False
    )


def test_initialize_plan_state_has_no_overall_score() -> None:
    state = initialize_plan_state(
        EpisodeRequest(topic="t", duration_minutes=30, language="zh", output_dir=Path("./o"))
    )
    assert "overall_score" not in state["critic"]


def test_save_state_json_still_strips_critic_control(tmp_path: Path) -> None:
    state = initialize_plan_state(
        EpisodeRequest(topic="t", duration_minutes=30, language="zh", output_dir=tmp_path)
    )
    path = save_state_json(state, tmp_path, "ep_v67")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "critic" not in raw
    assert "control" not in raw


class _Stub:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def generate(self, messages, **kwargs):  # noqa: ANN001, ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def test_legacy_critic_agent_rejects_overall_score() -> None:
    state = initialize_plan_state(
        EpisodeRequest(topic="t", duration_minutes=30, language="zh", output_dir=Path("./o"))
    )
    payload = {
        "critic": {
            "overall_score": 88,
            "scores": _planner_scores(88),
            "issues": [],
            "actions": [],
        }
    }
    with pytest.raises(AIServiceError, match="overall_score"):
        CriticAgent(llm_client=_Stub(payload)).run(state)

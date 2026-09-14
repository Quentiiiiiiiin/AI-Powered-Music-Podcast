"""v3.0 / v6.1：Planner Agent 契约测试。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.planner_agent import PlannerAgent
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


def _v4_segment(seg_id: str, order: int, name: str, duration: int) -> dict[str, Any]:
    return {
        "segment_id": seg_id,
        "order": order,
        "name": name,
        "target_duration_seconds": duration,
        "narrative_function": f"{name} role",
        "scene": f"{name} scene",
        "sonic_direction": ["electronic"],
        "lyrical_direction": ["night"],
        "anchor_tracks": [],
        "reference_material": [],
        "sequence_direction": [
            {"phase": "main", "function": "drive", "musical_direction": "forward"}
        ],
        "transition_to_next": "continue",
    }


def test_v30_planner_agent_writes_allowed_fields_only() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "meta": {
            "theme_description": "深夜陪伴与情绪递进",
            "theme_type": "",
            "theme_subject": "",
            "theme_relationship": "",
        },
        "global_constraints": {
            "energy_strategy": "克制递进",
            "sonic_world": ["chill"],
            "avoid": ["说教"],
        },
        "plan": {
            "segment_count": 2,
            "episode_direction": "平静到收束",
            "segments_design": "三段式结构",
        },
        "segments": [
            _v4_segment("seg_01", 1, "开场", 1200),
            _v4_segment("seg_02", 2, "中段", 1500),
        ],
    }
    agent = PlannerAgent(llm_client=_StubLLMClient(payload))

    next_state = agent.run(state)

    assert next_state["meta"]["theme_description"] == "深夜陪伴与情绪递进"
    assert next_state["global_constraints"]["energy_strategy"] == "克制递进"
    assert next_state["plan"]["segments_design"] == "三段式结构"
    assert next_state["plan"]["segment_count"] == 2
    assert len(next_state["segments"]) == 2
    assert next_state["segments"][0]["segment_id"] == "seg_01"
    assert next_state["segments"][0]["narrative_function"] == "开场 role"
    assert "playlist" in next_state["segments"][0]
    assert next_state["control"]["last_updated_by"] == "Planner"


def test_v30_planner_agent_rejects_forbidden_writes() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "segments": [
            {
                **_v4_segment("seg_01", 1, "开场", 1200),
                "playlist": [{"track": "A"}],
            },
        ],
    }
    agent = PlannerAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match="越权"):
        agent.run(state)

"""v3.0：Planner Agent 契约测试。"""
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


def test_v30_planner_agent_writes_allowed_fields_only() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "meta": {"theme_description": "深夜陪伴与情绪递进"},
        "global_constraints": {"tone": "克制", "avoid": ["说教"]},
        "plan": {
            "segments_design": "三段式结构",
            "emotion_curve": ["平静", "抬升", "收束"],
        },
        "segments": [
            {
                "segment_id": "seg_01",
                "order": 1,
                "name": "开场",
                "target_duration_seconds": 1200,
                "bpm_range": [90, 104],
                "mood": "舒缓",
                "segment_design": "铺垫主题",
            },
            {
                "segment_id": "seg_02",
                "order": 2,
                "name": "中段",
                "target_duration_seconds": 1500,
                "bpm_range": [100, 116],
                "mood": "推进",
                "segment_design": "提升能量",
            },
        ],
    }
    agent = PlannerAgent(llm_client=_StubLLMClient(payload))

    next_state = agent.run(state)

    assert next_state["meta"]["theme_description"] == "深夜陪伴与情绪递进"
    assert next_state["global_constraints"]["tone"] == "克制"
    assert next_state["plan"]["segments_design"] == "三段式结构"
    assert next_state["plan"]["emotion_curve"] == ["平静", "抬升", "收束"]
    assert len(next_state["segments"]) == 2
    assert next_state["segments"][0]["segment_id"] == "seg_01"
    assert next_state["segments"][0]["order"] == 1
    # Planner 不得写 segments[*].playlist，但初始化模板包含该字段
    assert "playlist" in next_state["segments"][0]
    assert next_state["control"]["last_updated_by"] == "Planner"


def test_v30_planner_agent_rejects_forbidden_writes() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "segments": [
            {
                "name": "开场",
                "target_duration_seconds": 1200,
                "bpm_range": [90, 104],
                "mood": "舒缓",
                "segment_design": "铺垫主题",
                "playlist": [{"track": "A"}],
            },
        ],
    }
    agent = PlannerAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match="越权"):
        agent.run(state)

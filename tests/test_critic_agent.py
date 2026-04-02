"""v3.0：Critic Agent 契约测试。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.critic_agent import CriticAgent
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


def test_v30_critic_agent_pass_false_requires_actions_and_sets_next_agent() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "critic": {
            "pass": False,
            "scores": {"coherence": 6, "emotion_flow": 5, "immersion": 6},
            "issues": [
                {
                    "type": "emotion_flow",
                    "location": "segments[0].playlist[0]",
                    "problem": "情绪跳跃过大",
                    "suggestion": "替换为过渡更平缓的歌曲",
                }
            ],
            "actions": [
                {
                    "target_agent": "Music Curator",
                    "instruction": "调整 playlist 的情绪曲线并减少 BPM 跳变。",
                }
            ],
        },
        "control": {"next_agent": "Music Curator"},
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)
    assert next_state["critic"]["pass"] is False
    assert len(next_state["critic"]["actions"]) == 1
    assert next_state["control"]["next_agent"] == "Music Curator"


def test_v30_critic_agent_pass_false_without_actions_raises() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "critic": {
            "pass": False,
            "scores": {"coherence": 6, "emotion_flow": 5, "immersion": 6},
            "issues": [],
            "actions": [],
        },
        "control": {"next_agent": "Music Curator"},
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"actions.*至少"):
        agent.run(state)


def test_v30_critic_agent_rejects_forbidden_plan_write() -> None:
    state = initialize_plan_state(_request())
    payload = {
        "plan": {"segments_design": "bad"},
        "critic": {
            "pass": True,
            "scores": {"coherence": 7, "emotion_flow": 7, "immersion": 7},
            "issues": [],
            "actions": [],
        },
        "control": {"next_agent": "Orchestrator"},
    }
    agent = CriticAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"越权写入顶层字段"):
        agent.run(state)


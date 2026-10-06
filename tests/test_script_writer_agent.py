"""v7.3：Script Writer Agent 契约（扁平 script_before）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.state import initialize_plan_state


class _StubLLMClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def _request(language: str) -> EpisodeRequest:
    return EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language=language,
        output_dir=Path("./output"),
    )


def _settings() -> Settings:
    return Settings(
        llm=LLMConfig(
            api_key="k",
            base_url="https://api.openai.com/v1",
            model="m",
            structured_output=False,
            output_max_retries=0,
        ),
        app=AppConfig(output_dir="./output"),
    )


def _state_with_tracks(language: str, n: int = 3) -> dict[str, Any]:
    state = initialize_plan_state(_request(language))
    playlist = [
        {
            "track": f"T{i}",
            "artist": f"A{i}",
            "selection_reason": "x",
            "sequence_role": "main",
            "planner_alignment": [],
            "transition_logic": "",
        }
        for i in range(n)
    ]
    state["segments"] = [
        {
            **state["segments"][0],
            "playlist": playlist,
            "script": {"segment_intro": None, "between_tracks": []},
        }
    ]
    return state


def test_v73_script_writer_writes_via_flat_cues() -> None:
    state = _state_with_tracks("zh", n=3)
    payload = {
        "script_before": [
            "欢迎来到 Luma Hits，今晚我们从温柔的节奏开始。",
            None,
            "接下来这首歌把情绪慢慢推高。",
        ]
    }
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload), settings=_settings())
    next_state = agent.run(state)

    assert next_state["segments"][0]["script"]["segment_intro"].startswith("欢迎来到")
    assert next_state["segments"][0]["script"]["between_tracks"][0]["text"] is None
    assert next_state["segments"][0]["script"]["between_tracks"][1]["text"].startswith("接下来")
    assert next_state["control"]["last_updated_by"] == "Script Writer"


def test_v73_script_writer_rejects_forbidden_top_level() -> None:
    state = _state_with_tracks("zh", n=1)
    payload = {"script_before": ["欢迎"], "segments": []}
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload), settings=_settings())
    with pytest.raises(AIServiceError, match="越权"):
        agent.run(state)


def test_v73_script_writer_rejects_language_mismatch() -> None:
    state = _state_with_tracks("en", n=1)
    payload = {"script_before": ["今晚我们从温柔的节奏开始。"]}
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload), settings=_settings())
    with pytest.raises(AIServiceError, match=r"语言不一致"):
        agent.run(state)


def test_v73_script_writer_allows_all_null() -> None:
    state = _state_with_tracks("zh", n=2)
    payload = {"script_before": [None, None]}
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload), settings=_settings())
    next_state = agent.run(state)
    assert next_state["segments"][0]["script"]["segment_intro"] is None
    assert next_state["segments"][0]["script"]["between_tracks"][0]["text"] is None

"""v3.0：Script Writer Agent 契约测试。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
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


def test_v30_script_writer_writes_script_fields_only() -> None:
    state = initialize_plan_state(_request("zh"))
    payload = {
        "segments": [
            {
                "script": {
                    "segment_intro": "欢迎来到 Luma Hits，今晚我们从温柔的节奏开始。",
                    "between_tracks": [
                        {"after_track_index": 0, "text": None},
                        {"after_track_index": 1, "text": "接下来这首歌把情绪慢慢推高。"},
                    ],
                }
            }
        ]
    }
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)

    assert next_state["segments"][0]["script"]["segment_intro"].startswith("欢迎来到")
    assert len(next_state["segments"][0]["script"]["between_tracks"]) == 2
    assert next_state["segments"][0]["script"]["between_tracks"][1]["text"].startswith("接下来")
    assert next_state["control"]["last_updated_by"] == "Script Writer"


def test_v30_script_writer_rejects_forbidden_playlist_write() -> None:
    state = initialize_plan_state(_request("zh"))
    payload = {"segments": [{"playlist": [], "script": {"segment_intro": "欢迎", "between_tracks": []}}]}
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"越权写入 segments\[0\]"):
        agent.run(state)


def test_v30_script_writer_rejects_language_mismatch() -> None:
    state = initialize_plan_state(_request("en"))
    # 给定 en-US，但提供中文 segment_intro
    payload = {
        "segments": [
            {
                "script": {
                    "segment_intro": "今晚我们从温柔的节奏开始。",
                    "between_tracks": [{"after_track_index": 0, "text": None}],
                }
            }
        ]
    }
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"语言不一致"):
        agent.run(state)


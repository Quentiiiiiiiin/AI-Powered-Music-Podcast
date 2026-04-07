"""v3.0：Music Curator Agent 契约测试。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
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


def test_v30_music_curator_writes_playlist_only_and_preserves_segment_fields() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "bpm_range": [90, 105],
            "mood": "舒缓",
            "segment_design": "选曲思路",
        },
        {
            "segment_id": "seg_02",
            "order": 2,
            "name": "中段",
            "target_duration_seconds": 1200,
            "bpm_range": [100, 116],
            "mood": "推进",
            "segment_design": "提升能量",
        },
    ]

    payload = {
        "segments": [
            {
                "playlist": [
                    {"track": "Track A", "artist": "Artist X", "bpm": 98},
                    {"track": "Track B", "artist": "Artist Y", "bpm": 103},
                ]
            },
            {
                "playlist": [{"track": "Track C", "artist": "Artist Z", "bpm": 110}],
            },
        ]
    }

    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)

    # 保留 Planner 写入的段落骨架字段
    assert next_state["segments"][0]["name"] == "开场"
    assert next_state["segments"][0]["segment_design"] == "选曲思路"
    assert next_state["segments"][0]["mood"] == "舒缓"

    # 写入 playlist
    assert len(next_state["segments"][0]["playlist"]) == 2
    assert next_state["segments"][0]["playlist"][0]["track"] == "Track A"
    assert next_state["segments"][1]["playlist"][0]["artist"] == "Artist Z"

    assert next_state["control"]["last_updated_by"] == "Music Curator"


def test_v35_music_curator_accepts_null_bpm() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "bpm_range": [90, 105],
            "mood": "舒缓",
            "segment_design": "选曲思路",
        },
    ]
    payload = {
        "segments": [
            {"playlist": [{"track": "Track A", "artist": "Artist X", "bpm": None}]},
        ],
    }
    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)
    assert next_state["segments"][0]["playlist"][0]["bpm"] is None


def test_v35_music_curator_rejects_non_int_bpm() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "bpm_range": [90, 105],
            "mood": "舒缓",
            "segment_design": "选曲思路",
        },
    ]
    payload = {"segments": [{"playlist": [{"track": "A", "artist": "B", "bpm": 98.5}]}]}
    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))
    with pytest.raises(AIServiceError, match="bpm"):
        agent.run(state)


def test_v30_music_curator_rejects_forbidden_script_write() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "bpm_range": [90, 105],
            "mood": "舒缓",
            "segment_design": "选曲思路",
        }
    ]

    payload = {"segments": [{"playlist": [], "script": {"segment_intro": "bad"}}]}
    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"越权写入 segments\[0\]"):
        agent.run(state)


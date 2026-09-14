"""v3.0 / v6.1：Music Curator Agent 契约测试。"""
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


def _pl(track: str, artist: str) -> dict[str, Any]:
    return {
        "track": track,
        "artist": artist,
        "selection_reason": "fits",
        "sequence_role": "main",
        "planner_alignment": ["sonic"],
        "transition_logic": "smooth",
    }


def test_v30_music_curator_writes_playlist_only_and_preserves_segment_fields() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "narrative_function": "选曲思路",
            "scene": "night",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
        },
        {
            "segment_id": "seg_02",
            "order": 2,
            "name": "中段",
            "target_duration_seconds": 1200,
            "narrative_function": "提升能量",
            "scene": "drive",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
        },
    ]

    payload = {
        "segments": [
            {"playlist": [_pl("Track A", "Artist X"), _pl("Track B", "Artist Y")]},
            {"playlist": [_pl("Track C", "Artist Z")]},
        ]
    }

    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))
    next_state = agent.run(state)

    assert next_state["segments"][0]["name"] == "开场"
    assert next_state["segments"][0]["narrative_function"] == "选曲思路"
    assert len(next_state["segments"][0]["playlist"]) == 2
    assert next_state["segments"][0]["playlist"][0]["track"] == "Track A"
    assert next_state["segments"][0]["playlist"][0]["selection_reason"] == "fits"
    assert "bpm" not in next_state["segments"][0]["playlist"][0]
    assert next_state["segments"][1]["playlist"][0]["artist"] == "Artist Z"
    assert next_state["control"]["last_updated_by"] == "Music Curator"


def test_v61_music_curator_rejects_bpm_field() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "narrative_function": "选曲思路",
            "scene": "",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
        },
    ]
    bad = _pl("Track A", "Artist X")
    bad["bpm"] = 98
    payload = {"segments": [{"playlist": [bad]}]}
    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))
    with pytest.raises(AIServiceError, match="越权"):
        agent.run(state)


def test_v30_music_curator_rejects_forbidden_script_write() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "narrative_function": "选曲思路",
            "scene": "",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
        }
    ]

    payload = {"segments": [{"playlist": [], "script": {"segment_intro": "bad"}}]}
    agent = MusicCuratorAgent(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"越权写入 segments\[0\]"):
        agent.run(state)

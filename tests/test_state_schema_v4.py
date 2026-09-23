"""v6.1：Planner/Curator Schema v4 sanitize + snapshot 不泄漏。"""
from __future__ import annotations

from pathlib import Path

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.storage.paths import build_episode_snapshot_from_state
from podcast_ai.modules.theme.music_curator_agent import sanitize_curator_patch
from podcast_ai.modules.theme.planner_agent import sanitize_planner_patch
from podcast_ai.modules.theme.state import (
    initialize_plan_state,
    merge_plan_state,
    validate_episode_snapshot_subset,
    validate_state_conforms_to_schema,
)


def _request() -> EpisodeRequest:
    return EpisodeRequest(
        topic="Night Drive",
        duration_minutes=60,
        language="zh",
        output_dir=Path("./output"),
    )


def _planner_segment(**overrides: object) -> dict:
    base = {
        "segment_id": "seg_01",
        "order": 1,
        "name": "Ignition",
        "target_duration_seconds": 780,
        "narrative_function": "Establish world",
        "scene": "Dashboard glow",
        "sonic_direction": ["cinematic"],
        "lyrical_direction": ["movement"],
        "anchor_tracks": [{"track": "Lose My Mind", "artist": "Don Toliver", "required": True}],
        "reference_material": [
            {"track": "Sweet Dreams", "artist": "Eurythmics", "purpose": "hypnotic"}
        ],
        "sequence_direction": [
            {"phase": "arrival", "function": "establish", "musical_direction": "spacious"}
        ],
        "transition_to_next": "Increase propulsion",
    }
    base.update(overrides)
    return base


def _playlist_item(**overrides: object) -> dict:
    base = {
        "track": "Lose My Mind",
        "artist": "Don Toliver",
        "selection_reason": "Fits nocturnal cinematic world",
        "sequence_role": "Opener",
        "planner_alignment": ["Matches sonic_direction cinematic"],
        "transition_logic": "Hands off into denser rhythm",
    }
    base.update(overrides)
    return base


def test_v61_initialize_state_is_v4_and_schema_conform() -> None:
    state = initialize_plan_state(_request())
    assert state["schema_version"] == "v4.0"
    assert "theme_type" in state["meta"]
    assert "energy_strategy" in state["global_constraints"]
    assert "segment_count" in state["plan"]
    assert "narrative_function" in state["segments"][0]
    assert "emotion_curve" not in state["plan"]
    assert "overall_bpm_range" not in state["meta"]
    validate_state_conforms_to_schema(state, agent_mode="multi_agent")


def test_v61_sanitize_planner_accepts_v4_and_rejects_playlist() -> None:
    patch = sanitize_planner_patch(
        {
            "meta": {
                "theme_description": "desc",
                "theme_type": "",
                "theme_subject": "",
                "theme_relationship": "",
            },
            "global_constraints": {
                "energy_strategy": "high",
                "sonic_world": ["electronic"],
                "avoid": ["ballads"],
            },
            "plan": {
                "segment_count": 1,
                "episode_direction": "forward",
                "segments_design": "one arc",
            },
            "segments": [_planner_segment()],
        }
    )
    assert patch["plan"]["segment_count"] == 1
    assert "playlist" not in patch["segments"][0]

    with pytest.raises(AIServiceError, match="越权"):
        sanitize_planner_patch({"segments": [_planner_segment(playlist=[{"track": "A"}])]})


def test_v61_sanitize_planner_rejects_legacy_segment_keys() -> None:
    with pytest.raises(AIServiceError, match="越权"):
        sanitize_planner_patch(
            {
                "segments": [
                    _planner_segment(mood="x", bpm_range=[90, 100], segment_design="old")
                ]
            }
        )


def test_v61_sanitize_curator_requires_explanation_fields() -> None:
    with pytest.raises(AIServiceError, match="缺少字段"):
        sanitize_curator_patch(
            {"segments": [{"playlist": [{"track": "A", "artist": "B"}]}]},
            expected_segments=1,
        )

    ok = sanitize_curator_patch(
        {"segments": [{"playlist": [_playlist_item()]}]},
        expected_segments=1,
    )
    item = ok["segments"][0]["playlist"][0]
    assert item["selection_reason"]
    assert item["planner_alignment"] == ["Matches sonic_direction cinematic"]


def test_v61_sanitize_curator_rejects_bpm() -> None:
    with pytest.raises(AIServiceError, match="越权"):
        sanitize_curator_patch(
            {"segments": [{"playlist": [_playlist_item(bpm=98)]}]},
            expected_segments=1,
        )


def test_v61_sanitize_curator_accepts_v4_without_bpm() -> None:
    ok = sanitize_curator_patch(
        {"segments": [{"playlist": [_playlist_item()]}]},
        expected_segments=1,
    )
    item = ok["segments"][0]["playlist"][0]
    assert "bpm" not in item
    assert item["selection_reason"]
    assert item["planner_alignment"] == ["Matches sonic_direction cinematic"]


def test_v61_snapshot_does_not_leak_v4_fields() -> None:
    state = initialize_plan_state(_request())
    state = merge_plan_state(
        state,
        {
            "segments": [
                {
                    **_planner_segment(name="开场"),
                    "playlist": [_playlist_item()],
                    "script": {
                        "segment_intro": "欢迎",
                        "between_tracks": [{"after_track_index": 0, "text": None}],
                    },
                }
            ]
        },
    )
    snap = build_episode_snapshot_from_state(state)
    validate_episode_snapshot_subset(snap)
    seg = snap["segments"][0]
    assert set(seg.keys()) == {
        "segment_id",
        "name",
        "target_duration_seconds",
        "playlists",
        "script",
    }
    assert "sonic_direction" not in seg
    assert "narrative_function" not in seg
    pl = seg["playlists"][0]
    assert set(pl.keys()) == {"track", "artist"}
    assert "bpm" not in pl
    assert "selection_reason" not in pl
    assert "planner_alignment" not in pl

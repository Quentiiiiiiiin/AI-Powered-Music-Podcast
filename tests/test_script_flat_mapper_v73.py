"""v7.3：扁平歌单索引、script_before sanitize/mapper、revision 保持。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings
from podcast_ai.infra.storage.paths import format_audit_agent_filename
from podcast_ai.modules.theme.agent_response_schemas import build_script_writer_response_schema
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_SCRIPT_WRITER, FilePlanAuditSink
from podcast_ai.modules.theme.script_flat_mapper import (
    apply_revision_preserve,
    build_flat_tracks,
    episode_to_local,
    flatten_script_before_from_state,
    format_flat_playlist_lines,
    local_to_episode_index,
    map_script_before_to_segments_patch,
    parse_action_episode_indices,
    total_track_count,
)
from podcast_ai.modules.theme.script_writer_agent import (
    ScriptWriterAgent,
    sanitize_script_before_patch,
)
from podcast_ai.modules.theme.state import initialize_plan_state


def _pl(track: str, artist: str) -> dict[str, Any]:
    return {
        "track": track,
        "artist": artist,
        "selection_reason": "fits",
        "sequence_role": "main",
        "planner_alignment": ["sonic"],
        "transition_logic": "smooth",
    }


def _two_seg_state() -> dict[str, Any]:
    state = initialize_plan_state(
        EpisodeRequest(topic="T", duration_minutes=60, language="zh", output_dir=Path("./output"))
    )
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "A",
            "target_duration_seconds": 600,
            "narrative_function": "open",
            "scene": "night",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
            "playlist": [_pl("Track A", "Artist A"), _pl("Track B", "Artist B")],
            "script": {"segment_intro": None, "between_tracks": []},
        },
        {
            "segment_id": "seg_02",
            "order": 2,
            "name": "B",
            "target_duration_seconds": 600,
            "narrative_function": "close",
            "scene": "dawn",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
            "playlist": [_pl("Track C", "Artist C")],
            "script": {"segment_intro": None, "between_tracks": []},
        },
    ]
    return state


def test_flatten_and_bidirectional_index() -> None:
    state = _two_seg_state()
    flat = build_flat_tracks(state["segments"])
    assert total_track_count(state["segments"]) == 3
    assert [t.episode_track_index for t in flat] == [0, 1, 2]
    assert flat[0].segment_id == "seg_01" and flat[0].track_index_in_segment == 0
    assert flat[2].segment_id == "seg_02" and flat[2].track_index_in_segment == 0
    assert local_to_episode_index(flat, segment_index=0, track_index=1) == 1
    assert episode_to_local(flat, 2).track == "Track C"
    lines = format_flat_playlist_lines(flat)
    assert "[0] Artist A – Track A   (seg_01)" in lines
    assert "[2] Artist C – Track C   (seg_02)" in lines


def test_schema_fixed_length_n() -> None:
    schema = build_script_writer_response_schema(4)
    arr = schema["properties"]["script_before"]
    assert arr["minItems"] == 4 and arr["maxItems"] == 4
    assert schema["required"] == ["script_before"]
    assert "segments" not in schema["properties"]
    with pytest.raises(ValueError):
        build_script_writer_response_schema(0)


def test_sanitize_rejects_wrong_length_and_extra_keys() -> None:
    with pytest.raises(AIServiceError, match="长度"):
        sanitize_script_before_patch({"script_before": [None]}, expected_tracks=2, language="zh")
    with pytest.raises(AIServiceError, match="越权"):
        sanitize_script_before_patch(
            {"script_before": [None, None], "segments": []},
            expected_tracks=2,
            language="zh",
        )


def test_sanitize_empty_string_to_null() -> None:
    out = sanitize_script_before_patch(
        {"script_before": ["欢迎来到 Luma Hits", "  ", None]},
        expected_tracks=3,
        language="zh-CN",
    )
    assert out["script_before"] == ["欢迎来到 Luma Hits", None, None]


def test_mapper_n1_intro_only() -> None:
    segments = [
        {
            "segment_id": "seg_01",
            "playlist": [_pl("Only", "Solo")],
            "script": {"segment_intro": None, "between_tracks": []},
        }
    ]
    patch = map_script_before_to_segments_patch(["开场问好"], segments)
    assert patch["segments"][0]["script"]["segment_intro"] == "开场问好"
    assert patch["segments"][0]["script"]["between_tracks"] == []


def test_mapper_cross_segment_and_last_before() -> None:
    state = _two_seg_state()
    # [0] intro seg1, [1] between after0, [2] intro seg2 (= last track before)
    cues = ["段一首前", None, "最后一首前收束"]
    patch = map_script_before_to_segments_patch(cues, state["segments"])
    s0 = patch["segments"][0]["script"]
    s1 = patch["segments"][1]["script"]
    assert s0["segment_intro"] == "段一首前"
    assert s0["between_tracks"] == [{"after_track_index": 0, "text": None}]
    assert s1["segment_intro"] == "最后一首前收束"
    assert s1["between_tracks"] == []


def test_mapper_all_null_ok() -> None:
    state = _two_seg_state()
    patch = map_script_before_to_segments_patch([None, None, None], state["segments"])
    assert patch["segments"][0]["script"]["segment_intro"] is None
    assert patch["segments"][0]["script"]["between_tracks"][0]["text"] is None
    assert patch["segments"][1]["script"]["segment_intro"] is None


def test_mapper_length_mismatch_fails() -> None:
    state = _two_seg_state()
    with pytest.raises(AIServiceError, match="禁止写入半套"):
        map_script_before_to_segments_patch([None, None], state["segments"])


def test_reverse_flatten_roundtrip() -> None:
    state = _two_seg_state()
    cues = ["开场", "曲间", "收束"]
    mapped = map_script_before_to_segments_patch(cues, state["segments"])
    for i, seg_patch in enumerate(mapped["segments"]):
        state["segments"][i]["script"] = seg_patch["script"]
    assert flatten_script_before_from_state(state) == cues


def test_revision_preserve_untouched() -> None:
    prev = ["A", "B", "C"]
    new = ["X", "Y", "Z"]
    out = apply_revision_preserve(new, prev, mutable_indices={1})
    assert out == ["A", "Y", "C"]


def test_parse_action_location_to_episode_index() -> None:
    state = _two_seg_state()
    flat = build_flat_tracks(state["segments"])
    actions = [
        {
            "target_agent": "Script Writer",
            "location": "segments[0].script.between_tracks[0]",
            "instruction": "fix",
        }
    ]
    assert parse_action_episode_indices(actions, flat) == {1}
    actions2 = [
        {
            "target_agent": "Script Writer",
            "location": "segments[1].script.segment_intro",
            "instruction": "x",
        }
    ]
    assert parse_action_episode_indices(actions2, flat) == {2}


class _StubLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        self.calls += 1
        return json.dumps(self._payload, ensure_ascii=False)


def _settings(tmp: Path) -> Settings:
    return Settings(
        app=AppConfig(output_dir=str(tmp)),
        llm=LLMConfig(
            api_key="k",
            base_url="https://api.openai.com/v1",
            model="m",
            structured_output=False,
            output_max_retries=0,
        ),
    )


def test_writer_agent_maps_flat_to_script(tmp_path: Path) -> None:
    state = _two_seg_state()
    payload = {"script_before": ["欢迎来到 Luma Hits", None, "今晚到这里，下一首结束。"]}
    agent = ScriptWriterAgent(llm_client=_StubLLM(payload), settings=_settings(tmp_path))
    next_state = agent.run(state)
    assert next_state["segments"][0]["script"]["segment_intro"].startswith("欢迎")
    assert next_state["segments"][0]["script"]["between_tracks"][0]["text"] is None
    assert next_state["segments"][1]["script"]["segment_intro"].startswith("今晚")
    assert next_state["control"]["last_updated_by"] == "Script Writer"
    # playlist 不被污染
    assert next_state["segments"][0]["playlist"][0]["track"] == "Track A"


def test_writer_agent_n0_fails(tmp_path: Path) -> None:
    state = initialize_plan_state(
        EpisodeRequest(topic="T", duration_minutes=60, language="zh", output_dir=tmp_path)
    )
    agent = ScriptWriterAgent(
        llm_client=_StubLLM({"script_before": []}),
        settings=_settings(tmp_path),
    )
    with pytest.raises(AIServiceError, match="N=0"):
        agent.run(state)


def test_writer_revision_preserves_other_cues(tmp_path: Path) -> None:
    state = _two_seg_state()
    # 先落上一版
    mapped = map_script_before_to_segments_patch(
        ["开场甲", "曲间乙", "收束丙"],
        state["segments"],
    )
    for i, sp in enumerate(mapped["segments"]):
        state["segments"][i]["script"] = sp["script"]
    state["critic"] = {
        "actions": [
            {
                "target_agent": "Script Writer",
                "location": "segments[0].script.between_tracks[0]",
                "instruction": "改曲间",
            }
        ]
    }
    # 模型试图改全部；系统应只允许 index=1 变化
    payload = {"script_before": ["被改开场", "新曲间文案", "被改收束"]}
    agent = ScriptWriterAgent(llm_client=_StubLLM(payload), settings=_settings(tmp_path))
    next_state = agent.run(state, mode="revision")
    assert next_state["segments"][0]["script"]["segment_intro"] == "开场甲"
    assert next_state["segments"][0]["script"]["between_tracks"][0]["text"] == "新曲间文案"
    assert next_state["segments"][1]["script"]["segment_intro"] == "收束丙"


def test_writer_audit_keeps_flat_and_mapped(tmp_path: Path) -> None:
    state = _two_seg_state()
    rid = str(state["meta"]["request_id"])
    sink = FilePlanAuditSink(tmp_path / "audit")
    payload = {"script_before": ["欢迎听众们", None, None]}
    agent = ScriptWriterAgent(llm_client=_StubLLM(payload), settings=_settings(tmp_path))
    agent.run(state, audit_sink=sink, round_iteration=2)
    path = tmp_path / "audit" / format_audit_agent_filename(2, AUDIT_SLUG_SCRIPT_WRITER)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["request_id"] == rid
    assert data["parsed_patch"]["script_before"][0].startswith("欢迎")
    assert "segment_intro" in data["mapped_patch"]["segments"][0]["script"]

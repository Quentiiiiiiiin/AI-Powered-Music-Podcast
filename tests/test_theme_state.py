"""v3.0：PlanState schema 与工具函数测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.state import (
    DEFAULT_MAX_ITERATIONS,
    assert_plan_state_valid,
    get_missing_required_fields,
    initialize_plan_state,
    merge_plan_state,
    validate_state_conforms_to_schema,
    validate_plan_state_schema,
)


def _request() -> EpisodeRequest:
    return EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language="zh",
        output_dir=Path("./output"),
    )


def test_v30_initialize_plan_state_defaults() -> None:
    state = initialize_plan_state(_request())
    assert state["schema_version"] == "v4.0"
    assert state["meta"]["theme"] == "Late Night Chill"
    assert state["meta"]["language"] == "zh-CN"
    assert state["meta"]["target_duration_seconds"] == 3600
    assert state["control"]["max_iterations"] == DEFAULT_MAX_ITERATIONS
    assert state["control"]["next_agent"] == "Planner"
    assert state["control"]["status"] == "draft"
    assert state["control"]["failed_stage"] is None


def test_v60_failed_stage_string_conforms_to_schema() -> None:
    """staged 失败路径：control.failed_stage 可为 string。"""
    state = initialize_plan_state(_request())
    state["control"]["status"] = "stage_failed"
    state["control"]["failed_stage"] = "music_curator"
    validate_state_conforms_to_schema(state, agent_mode="multi_agent")


def test_v30_merge_plan_state_is_field_level_and_non_destructive() -> None:
    original = initialize_plan_state(_request())
    patch = {
        "control": {"iteration": 1, "last_updated_by": "planner"},
        "plan": {"episode_direction": "warm -> peak -> close"},
    }
    merged = merge_plan_state(original, patch)

    assert original["control"]["iteration"] == 1
    assert merged["control"]["iteration"] == 1
    assert merged["control"]["last_updated_by"] == "planner"
    assert merged["plan"]["episode_direction"] == "warm -> peak -> close"


def test_v30_required_and_schema_validation() -> None:
    state = initialize_plan_state(_request())
    ok, errors = validate_plan_state_schema(state)
    assert ok is True
    assert errors == []

    bad_state = {"meta": {}}
    missing = get_missing_required_fields(bad_state)
    assert "schema_version" in missing
    assert "control" in missing
    assert "meta.theme" in missing


def test_v30_assert_plan_state_valid_raises_on_invalid_state() -> None:
    bad_state = initialize_plan_state(_request())
    bad_state["control"]["max_iterations"] = 0

    with pytest.raises(AIServiceError, match="control.max_iterations"):
        assert_plan_state_valid(bad_state)


def test_v30_validate_plan_state_schema_checks_nested_types() -> None:
    state = initialize_plan_state(_request())
    state["meta"]["theme_type"] = 123  # type: ignore[assignment]
    state["critic"]["scores"]["theme_definition"] = "bad"  # type: ignore[assignment]
    ok, errors = validate_plan_state_schema(state)
    assert ok is False
    assert any("meta.theme_type" in e for e in errors)
    assert any("critic.scores.theme_definition" in e for e in errors)


def test_v30_merge_list_of_dict_merges_by_index() -> None:
    state = initialize_plan_state(_request())
    state["segments"] = [
        {"segment_id": "seg_01", "name": "开场", "narrative_function": "舒缓"},
        {"segment_id": "seg_02", "name": "中段", "narrative_function": "推进"},
    ]
    merged = merge_plan_state(state, {"segments": [{"playlist": [{"track": "A", "artist": "X"}]}, {"playlist": []}]})
    assert merged["segments"][0]["name"] == "开场"
    assert merged["segments"][0]["narrative_function"] == "舒缓"
    assert merged["segments"][0]["playlist"][0]["track"] == "A"
    assert merged["segments"][1]["name"] == "中段"
    assert merged["segments"][1]["playlist"] == []


def test_merge_segments_shorter_patch_does_not_truncate() -> None:
    """Planner revision 只回部分章节时，不得截断丢掉未出现的尾部章节。"""
    state = initialize_plan_state(_request())
    state["segments"] = [
        {"segment_id": "seg_01", "name": "开场", "sonic_direction": ["a"]},
        {"segment_id": "seg_02", "name": "中段", "sonic_direction": ["b"]},
        {"segment_id": "seg_03", "name": "收束", "sonic_direction": ["c"]},
    ]
    merged = merge_plan_state(
        state,
        {
            "segments": [
                {"segment_id": "seg_01", "sonic_direction": ["a2"]},
                {"segment_id": "seg_03", "sonic_direction": ["c2"]},
            ]
        },
    )
    assert len(merged["segments"]) == 3
    assert merged["segments"][0]["segment_id"] == "seg_01"
    assert merged["segments"][0]["sonic_direction"] == ["a2"]
    assert merged["segments"][0]["name"] == "开场"
    # 下标 1 仍按索引合并（可能被误写入的 patch[1] 覆盖字段），但不得删除下标 2
    assert merged["segments"][2]["segment_id"] == "seg_03"
    assert merged["segments"][2]["name"] == "收束"
    assert merged["segments"][2]["sonic_direction"] == ["c"]


def test_merge_playlist_shorter_patch_still_truncates() -> None:
    """playlist 回修变短时仍截断，避免旧曲尾巴残留。"""
    state = initialize_plan_state(_request())
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "playlist": [
                {"track": "A", "artist": "X"},
                {"track": "B", "artist": "Y"},
                {"track": "C", "artist": "Z"},
            ],
        }
    ]
    merged = merge_plan_state(
        state,
        {
            "segments": [
                {
                    "playlist": [
                        {"track": "A2", "artist": "X"},
                        {"track": "B2", "artist": "Y"},
                    ]
                }
            ]
        },
    )
    assert len(merged["segments"]) == 1
    assert [t["track"] for t in merged["segments"][0]["playlist"]] == ["A2", "B2"]


# ---------- v3.1：state_schema.json 同构校验 ----------


def test_v31_validate_state_conforms_to_schema_allows_critic_control_null_in_single_agent() -> None:
    state = initialize_plan_state(_request())
    state.pop("critic")
    state.pop("control")
    validate_state_conforms_to_schema(state, agent_mode="single_agent")


def test_v31_validate_state_conforms_to_schema_rejects_critic_control_null_in_multi_agent() -> None:
    state = initialize_plan_state(_request())
    state["critic"] = None
    state["control"] = None
    with pytest.raises(AIServiceError):
        validate_state_conforms_to_schema(state, agent_mode="multi_agent")


def test_v37_validate_state_conforms_to_schema_rejects_critic_control_presence_in_single_agent() -> None:
    state = initialize_plan_state(_request())
    with pytest.raises(AIServiceError, match="single_agent state 顶层字段不匹配"):
        validate_state_conforms_to_schema(state, agent_mode="single_agent")


def test_v31_validate_state_conforms_to_schema_allows_between_tracks_text_string() -> None:
    """between_tracks[*].text 在模板中为 null，但实际允许为字符串或 null（交给 agent 决定）。"""
    state = initialize_plan_state(_request())
    state["segments"][0]["script"]["between_tracks"][0]["text"] = "下一首歌稍微推高情绪。"
    validate_state_conforms_to_schema(state, agent_mode="multi_agent")


def test_v73_validate_state_conforms_to_schema_allows_null_segment_intro() -> None:
    """v7.3：扁平串词空槽映射后 segment_intro 可为 null，落盘校验须通过。"""
    state = initialize_plan_state(_request())
    state["segments"][0]["script"]["segment_intro"] = None
    state["segments"][0]["script"]["between_tracks"][0]["text"] = None
    validate_state_conforms_to_schema(state, agent_mode="multi_agent")

    state["segments"][0]["script"]["segment_intro"] = "段首可选串词。"
    validate_state_conforms_to_schema(state, agent_mode="multi_agent")

"""
v3.4：OpenRouter / OpenAI 兼容的 `response_format.json_schema`（strict）定义。

与 planner / music_curator / script_writer / critic 的 sanitize 白名单一致；
Planner 根上四字段在 strict 下须始终出现，增量场景用 JSON null 表示“本字段不更新”（见 sanitize_planner_patch）。
Music Curator / Script Writer 的 segments 长度随编排变化，故提供按段落数生成的 builder。
"""
from __future__ import annotations

from typing import Any


def build_openrouter_response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """与仓库 `openrouter_structured_output.json` 中 `response_format` 形态一致（与 `messages` 并列传入 chat/completions）。"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def _nullable_strict_object(inner: dict[str, Any]) -> dict[str, Any]:
    """strict 下可选对象：用 null 表示省略整块更新。"""
    return {
        "anyOf": [
            {"type": "null"},
            inner,
        ]
    }


_PLANNER_ANCHOR_TRACK: dict[str, Any] = {
    "type": "object",
    "properties": {
        "track": {"type": "string"},
        "artist": {"type": "string"},
        "required": {"type": "boolean"},
    },
    "required": ["track", "artist", "required"],
    "additionalProperties": False,
}

_PLANNER_REFERENCE_MATERIAL: dict[str, Any] = {
    "type": "object",
    "properties": {
        "track": {"type": "string"},
        "artist": {"type": "string"},
        "purpose": {"type": "string"},
    },
    "required": ["track", "artist", "purpose"],
    "additionalProperties": False,
}

_PLANNER_SEQUENCE_DIRECTION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phase": {"type": "string"},
        "function": {"type": "string"},
        "musical_direction": {"type": "string"},
    },
    "required": ["phase", "function", "musical_direction"],
    "additionalProperties": False,
}

# v6.1 / Schema_Planner_v4：段落规划字段（不含 playlist/script）
_PLANNER_SEGMENT_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segment_id": {"type": "string"},
        "order": {"type": "integer"},
        "name": {"type": "string"},
        "target_duration_seconds": {"type": "integer"},
        "narrative_function": {"type": "string"},
        "scene": {"type": "string"},
        "sonic_direction": {"type": "array", "items": {"type": "string"}},
        "lyrical_direction": {"type": "array", "items": {"type": "string"}},
        "anchor_tracks": {"type": "array", "items": _PLANNER_ANCHOR_TRACK},
        "reference_material": {"type": "array", "items": _PLANNER_REFERENCE_MATERIAL},
        "sequence_direction": {"type": "array", "items": _PLANNER_SEQUENCE_DIRECTION},
        "transition_to_next": {"type": "string"},
    },
    "required": [
        "segment_id",
        "order",
        "name",
        "target_duration_seconds",
        "narrative_function",
        "scene",
        "sonic_direction",
        "lyrical_direction",
        "anchor_tracks",
        "reference_material",
        "sequence_direction",
        "transition_to_next",
    ],
    "additionalProperties": False,
}

# Planner：根结构仅四键；与 sanitize 允许键一致，null 表示不合并该键（见 planner_agent.sanitize_planner_patch）
PLANNER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "meta": _nullable_strict_object(
            {
                "type": "object",
                "properties": {
                    "theme_description": {"type": "string"},
                    "theme_type": {"type": "string"},
                    "theme_subject": {"type": "string"},
                    "theme_relationship": {"type": "string"},
                },
                "required": [
                    "theme_description",
                    "theme_type",
                    "theme_subject",
                    "theme_relationship",
                ],
                "additionalProperties": False,
            },
        ),
        "global_constraints": _nullable_strict_object(
            {
                "type": "object",
                "properties": {
                    "energy_strategy": {"type": "string"},
                    "sonic_world": {"type": "array", "items": {"type": "string"}},
                    "avoid": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["energy_strategy", "sonic_world", "avoid"],
                "additionalProperties": False,
            },
        ),
        "plan": _nullable_strict_object(
            {
                "type": "object",
                "properties": {
                    "segment_count": {"type": "integer"},
                    "episode_direction": {"type": "string"},
                    "segments_design": {"type": "string"},
                },
                "required": ["segment_count", "episode_direction", "segments_design"],
                "additionalProperties": False,
            },
        ),
        "segments": _nullable_strict_object(
            {
                "type": "array",
                "items": _PLANNER_SEGMENT_ITEM,
            },
        ),
    },
    "required": ["meta", "global_constraints", "plan", "segments"],
    "additionalProperties": False,
}

_CRITIC_ISSUE_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "severity": {"type": "string", "enum": ["minor", "critical"]},
        "location": {"type": "string"},
        "problem": {"type": "string"},
        "listener_impact": {"type": "string"},
        "suggestion": {"type": "string"},
    },
    "required": ["type", "severity", "location", "problem", "listener_impact", "suggestion"],
    "additionalProperties": False,
}

_CRITIC_ACTION_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_agent": {"type": "string"},
        "location": {"type": "string"},
        "instruction": {"type": "string"},
    },
    "required": ["target_agent", "location", "instruction"],
    "additionalProperties": False,
}

# v6.5：Planner Critic 各维满分 100
_CRITIC_SCORE_PROPS: dict[str, Any] = {
    "theme_definition": {"type": "integer", "minimum": 0, "maximum": 100},
    "theme_relationship": {"type": "integer", "minimum": 0, "maximum": 100},
    "musical_concept": {"type": "integer", "minimum": 0, "maximum": 100},
    "segment_differentiation": {"type": "integer", "minimum": 0, "maximum": 100},
    "sequence_narrative": {"type": "integer", "minimum": 0, "maximum": 100},
    "curator_actionability": {"type": "integer", "minimum": 0, "maximum": 100},
    "creative_freedom": {"type": "integer", "minimum": 0, "maximum": 100},
}

_CRITIC_BODY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": _CRITIC_SCORE_PROPS,
            "required": list(_CRITIC_SCORE_PROPS.keys()),
            "additionalProperties": False,
        },
        "issues": {"type": "array", "items": _CRITIC_ISSUE_ITEM},
        "actions": {"type": "array", "items": _CRITIC_ACTION_ITEM},
    },
    "required": ["scores", "issues", "actions"],
    "additionalProperties": False,
}

# v6.4 / v6.7：Planner Critic（无 overall_score；legacy 与 staged planner/writer 共用）
CRITIC_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"critic": _CRITIC_BODY_SCHEMA},
    "required": ["critic"],
    "additionalProperties": False,
}

CRITIC_STAGED_RESPONSE_SCHEMA: dict[str, Any] = CRITIC_RESPONSE_SCHEMA

# v6.6 / v6.7：Curator Critic（actions 含 location，与 Planner 对齐）
_CURATOR_CRITIC_ISSUE_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "severity": {"type": "string", "enum": ["minor", "major", "critical"]},
        "location": {"type": "string"},
        "problem": {"type": "string"},
        "reason": {"type": "string"},
        "suggestion": {"type": "string"},
    },
    "required": ["type", "severity", "location", "problem", "reason", "suggestion"],
    "additionalProperties": False,
}

_CURATOR_CRITIC_ACTION_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_agent": {"type": "string"},
        "location": {"type": "string"},
        "instruction": {"type": "string"},
    },
    "required": ["target_agent", "location", "instruction"],
    "additionalProperties": False,
}

_CURATOR_CRITIC_SCORE_PROPS: dict[str, Any] = {
    "planner_alignment": {"type": "integer", "minimum": 0, "maximum": 100},
    "thematic_relevance": {"type": "integer", "minimum": 0, "maximum": 100},
    "sequence_coherence": {"type": "integer", "minimum": 0, "maximum": 100},
    "audience_listening_quality": {"type": "integer", "minimum": 0, "maximum": 100},
    "track_fitness": {"type": "integer", "minimum": 0, "maximum": 100},
}

_CURATOR_CRITIC_BODY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": _CURATOR_CRITIC_SCORE_PROPS,
            "required": list(_CURATOR_CRITIC_SCORE_PROPS.keys()),
            "additionalProperties": False,
        },
        "issues": {"type": "array", "items": _CURATOR_CRITIC_ISSUE_ITEM},
        "actions": {"type": "array", "items": _CURATOR_CRITIC_ACTION_ITEM},
    },
    "required": ["scores", "issues", "actions"],
    "additionalProperties": False,
}

CURATOR_CRITIC_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"critic": _CURATOR_CRITIC_BODY_SCHEMA},
    "required": ["critic"],
    "additionalProperties": False,
}

_PLAYLIST_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "track": {"type": "string"},
        "artist": {"type": "string"},
        "selection_reason": {"type": "string"},
        "sequence_role": {"type": "string"},
        "planner_alignment": {"type": "array", "items": {"type": "string"}},
        "transition_logic": {"type": "string"},
    },
    "required": [
        "track",
        "artist",
        "selection_reason",
        "sequence_role",
        "planner_alignment",
        "transition_logic",
    ],
    "additionalProperties": False,
}

_BETWEEN_TRACK_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "after_track_index": {"type": "integer"},
        "text": {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ],
        },
    },
    "required": ["after_track_index", "text"],
    "additionalProperties": False,
}

_SCRIPT_OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segment_intro": {"type": "string"},
        "between_tracks": {
            "type": "array",
            "items": _BETWEEN_TRACK_ITEM,
        },
    },
    "required": ["segment_intro", "between_tracks"],
    "additionalProperties": False,
}

# v3.7 / v6.1：single_agent 直接输出 PlanState 子集（Planner+Curator+Writer 字段；无 critic/control）
SINGLE_AGENT_STATE_SUBSET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "meta": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "theme": {"type": "string"},
                "theme_description": {"type": "string"},
                "language": {"type": "string"},
                "target_duration_seconds": {"type": "integer"},
                "theme_type": {"type": "string"},
                "theme_subject": {"type": "string"},
                "theme_relationship": {"type": "string"},
            },
            "required": [
                "request_id",
                "theme",
                "theme_description",
                "language",
                "target_duration_seconds",
                "theme_type",
                "theme_subject",
                "theme_relationship",
            ],
            "additionalProperties": False,
        },
        "global_constraints": {
            "type": "object",
            "properties": {
                "energy_strategy": {"type": "string"},
                "sonic_world": {"type": "array", "items": {"type": "string"}},
                "avoid": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["energy_strategy", "sonic_world", "avoid"],
            "additionalProperties": False,
        },
        "plan": {
            "type": "object",
            "properties": {
                "segment_count": {"type": "integer"},
                "episode_direction": {"type": "string"},
                "segments_design": {"type": "string"},
            },
            "required": ["segment_count", "episode_direction", "segments_design"],
            "additionalProperties": False,
        },
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_id": {"type": "string"},
                    "order": {"type": "integer"},
                    "name": {"type": "string"},
                    "target_duration_seconds": {"type": "integer"},
                    "narrative_function": {"type": "string"},
                    "scene": {"type": "string"},
                    "sonic_direction": {"type": "array", "items": {"type": "string"}},
                    "lyrical_direction": {"type": "array", "items": {"type": "string"}},
                    "anchor_tracks": {"type": "array", "items": _PLANNER_ANCHOR_TRACK},
                    "reference_material": {"type": "array", "items": _PLANNER_REFERENCE_MATERIAL},
                    "sequence_direction": {"type": "array", "items": _PLANNER_SEQUENCE_DIRECTION},
                    "transition_to_next": {"type": "string"},
                    "playlist": {
                        "type": "array",
                        "items": _PLAYLIST_ITEM,
                    },
                    "script": _SCRIPT_OBJECT,
                },
                "required": [
                    "segment_id",
                    "order",
                    "name",
                    "target_duration_seconds",
                    "narrative_function",
                    "scene",
                    "sonic_direction",
                    "lyrical_direction",
                    "anchor_tracks",
                    "reference_material",
                    "sequence_direction",
                    "transition_to_next",
                    "playlist",
                    "script",
                ],
                "additionalProperties": False,
            },
            "minItems": 1,
        },
    },
    "required": ["schema_version", "meta", "global_constraints", "plan", "segments"],
    "additionalProperties": False,
}


def build_music_curator_response_schema(segment_count: int) -> dict[str, Any]:
    if segment_count < 0:
        raise ValueError("segment_count 必须 >= 0")
    segment_item: dict[str, Any] = {
        "type": "object",
        "properties": {
            "playlist": {"type": "array", "items": _PLAYLIST_ITEM},
        },
        "required": ["playlist"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": segment_item,
                "minItems": segment_count,
                "maxItems": segment_count,
            },
        },
        "required": ["segments"],
        "additionalProperties": False,
    }


def build_script_writer_response_schema(segment_count: int) -> dict[str, Any]:
    if segment_count < 0:
        raise ValueError("segment_count 必须 >= 0")
    segment_item: dict[str, Any] = {
        "type": "object",
        "properties": {
            "script": _SCRIPT_OBJECT,
        },
        "required": ["script"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": segment_item,
                "minItems": segment_count,
                "maxItems": segment_count,
            },
        },
        "required": ["segments"],
        "additionalProperties": False,
    }

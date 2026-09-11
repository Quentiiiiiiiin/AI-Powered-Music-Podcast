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


_PLANNER_SEGMENT_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segment_id": {"type": "string"},
        "order": {"type": "integer"},
        "name": {"type": "string"},
        "target_duration_seconds": {"type": "integer"},
        "bpm_range": {
            "type": "array",
            "items": {"type": "integer"},
        },
        "mood": {"type": "string"},
        "segment_design": {"type": "string"},
    },
    "required": [
        "segment_id",
        "order",
        "name",
        "target_duration_seconds",
        "bpm_range",
        "mood",
        "segment_design",
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
                },
                "required": ["theme_description"],
                "additionalProperties": False,
            },
        ),
        "global_constraints": _nullable_strict_object(
            {
                "type": "object",
                "properties": {
                    "tone": {"type": "string"},
                    "language_style": {"type": "string"},
                    "avoid": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["tone", "language_style", "avoid"],
                "additionalProperties": False,
            },
        ),
        "plan": _nullable_strict_object(
            {
                "type": "object",
                "properties": {
                    "segments_design": {"type": "string"},
                    "emotion_curve": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["segments_design", "emotion_curve"],
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
        "location": {"type": "string"},
        "problem": {"type": "string"},
        "suggestion": {"type": "string"},
    },
    "required": ["type", "location", "problem", "suggestion"],
    "additionalProperties": False,
}

_CRITIC_ACTION_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_agent": {"type": "string"},
        "instruction": {"type": "string"},
    },
    "required": ["target_agent", "instruction"],
    "additionalProperties": False,
}

CRITIC_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "critic": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "scores": {
                    "type": "object",
                    "properties": {
                        "coherence": {"type": "integer", "minimum": 0, "maximum": 35},
                        "emotion_flow": {"type": "integer", "minimum": 0, "maximum": 35},
                        "immersion": {"type": "integer", "minimum": 0, "maximum": 30},
                    },
                    "required": ["coherence", "emotion_flow", "immersion"],
                    "additionalProperties": False,
                },
                "issues": {"type": "array", "items": _CRITIC_ISSUE_ITEM},
                "actions": {"type": "array", "items": _CRITIC_ACTION_ITEM},
            },
            "required": ["pass", "scores", "issues", "actions"],
            "additionalProperties": False,
        },
        "control": {
            "type": "object",
            "properties": {
                "next_agent": {"type": "string"},
            },
            "required": ["next_agent"],
            "additionalProperties": False,
        },
    },
    "required": ["critic", "control"],
    "additionalProperties": False,
}

# v6.0 staged：Critic 仅做阶段内评估，禁止 control.next_agent
CRITIC_STAGED_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "critic": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "scores": {
                    "type": "object",
                    "properties": {
                        "coherence": {"type": "integer", "minimum": 0, "maximum": 35},
                        "emotion_flow": {"type": "integer", "minimum": 0, "maximum": 35},
                        "immersion": {"type": "integer", "minimum": 0, "maximum": 30},
                    },
                    "required": ["coherence", "emotion_flow", "immersion"],
                    "additionalProperties": False,
                },
                "issues": {"type": "array", "items": _CRITIC_ISSUE_ITEM},
                "actions": {"type": "array", "items": _CRITIC_ACTION_ITEM},
            },
            "required": ["pass", "scores", "issues", "actions"],
            "additionalProperties": False,
        },
    },
    "required": ["critic"],
    "additionalProperties": False,
}

_PLAYLIST_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "track": {"type": "string"},
        "artist": {"type": "string"},
        # 未知 BPM 时允许 null（与 Curator prompt / sanitize 一致）
        "bpm": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["track", "artist", "bpm"],
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

# v3.7：single_agent 直接输出 PlanState 子集（仅五个顶层键）
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
                "overall_bpm_range": {
                    "anyOf": [
                        {"type": "null"},
                        {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                    ],
                },
            },
            "required": [
                "request_id",
                "theme",
                "theme_description",
                "language",
                "target_duration_seconds",
                "overall_bpm_range",
            ],
            "additionalProperties": False,
        },
        "global_constraints": {
            "type": "object",
            "properties": {
                "tone": {"type": "string"},
                "language_style": {"type": "string"},
                "avoid": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tone", "language_style", "avoid"],
            "additionalProperties": False,
        },
        "plan": {
            "type": "object",
            "properties": {
                "segments_design": {"type": "string"},
                "emotion_curve": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["segments_design", "emotion_curve"],
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
                    "bpm_range": {
                        "anyOf": [
                            {"type": "null"},
                            {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                        ],
                    },
                    "mood": {"type": "string"},
                    "segment_design": {"type": "string"},
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
                    "bpm_range",
                    "mood",
                    "segment_design",
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

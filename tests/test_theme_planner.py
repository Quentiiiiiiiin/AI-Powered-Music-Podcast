"""v2.2：ThemePlanner 解析稳定性回归测试。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.llm_planner import ThemePlanner


class _StubLLMClient:
    """单测用 LLM stub：返回预设 JSON 文本。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def test_v22_theme_planner_parses_strict_json_schema() -> None:
    """v2.2：符合强约束的 JSON 可被稳定解析为 EpisodePlan。"""
    payload = {
        "style_description": "Late-night warm electronic flow",
        "overall_bpm_range": None,
        "segments": [
            {
                "name": "开场",
                "target_duration_seconds": 1600,
                "bpm_range": None,
                "mood": "慢热、柔和",
                "host_script": "欢迎来到今晚的 Luma Hits，我们从一段温柔节奏开始。",
                "target_playlist": [
                    {
                        "recommended_tracks": ["Track A - Artist X", "Track B - Artist Y"],
                        "search_hints": {"keywords": ["late night", "soft electronic"]},
                        "host_script_between_songs": None,
                    }
                ],
            },
            {
                "name": "中段",
                "target_duration_seconds": 1800,
                "bpm_range": [102, 118],
                "mood": "渐进、抬升",
                "host_script": "接下来让节奏再向前一点，保持夜晚的流动感。",
                "target_playlist": [
                    {
                        "recommended_tracks": ["Track C - Artist Z"],
                        "search_hints": {},
                        "host_script_between_songs": "这一首会把能量慢慢拉满。",
                    }
                ],
            },
        ],
    }
    request = EpisodeRequest(
        topic="Late Night Chill Electronic",
        duration_minutes=60,
        language="zh",
        output_dir=Path("./output"),
    )
    planner = ThemePlanner(llm_client=_StubLLMClient(payload))

    plan = planner.generate_plan(request)

    assert len(plan.segments) >= 2
    for seg in plan.segments:
        assert isinstance(seg.host_script, str)
        assert seg.host_script.strip() != ""
        assert isinstance(seg.target_playlist, list)
        assert len(seg.target_playlist) >= 1
        for item in seg.target_playlist:
            assert isinstance(item.recommended_tracks, list)
            assert all(isinstance(x, str) for x in item.recommended_tracks)
            assert isinstance(item.search_hints, dict)

    actual_sum = sum(seg.target_duration_seconds for seg in plan.segments)
    target_sum = request.duration_minutes * 60
    tolerance = target_sum * 0.10
    assert abs(actual_sum - target_sum) <= tolerance


def test_v22_theme_planner_search_hints_missing_or_invalid_fallback_to_empty_dict() -> None:
    """v2.2：search_hints 缺失或类型错误时，解析结果稳定回退为 {}。"""
    payload = {
        "style_description": "Fallback test",
        "overall_bpm_range": [90, 120],
        "segments": [
            {
                "name": "开场",
                "target_duration_seconds": 300,
                "bpm_range": [90, 100],
                "mood": "warm",
                "host_script": "欢迎收听。",
                "target_playlist": [
                    {
                        "recommended_tracks": ["Track A - Artist X"],
                    },
                    {
                        "recommended_tracks": ["Track B - Artist Y"],
                        "search_hints": "not-an-object",
                    },
                ],
            }
        ],
    }
    request = EpisodeRequest(
        topic="Fallback Case",
        duration_minutes=10,
        language="zh",
        output_dir=Path("./output"),
    )
    planner = ThemePlanner(llm_client=_StubLLMClient(payload))

    plan = planner.generate_plan(request)

    assert len(plan.segments) == 1
    assert len(plan.segments[0].target_playlist) == 2
    assert plan.segments[0].target_playlist[0].search_hints == {}
    assert plan.segments[0].target_playlist[1].search_hints == {}


def test_v22_theme_planner_error_message_for_empty_host_script() -> None:
    """v2.2：host_script 缺失/为空时，应抛带定位信息的 AIServiceError。"""
    payload = {
        "style_description": "bad host_script",
        "overall_bpm_range": [90, 120],
        "segments": [
            {
                "name": "开场",
                "target_duration_seconds": 300,
                "bpm_range": [90, 100],
                "mood": "warm",
                "host_script": "",
                "target_playlist": [{"recommended_tracks": ["Track A - Artist X"], "search_hints": {}}],
            }
        ],
    }
    request = EpisodeRequest(
        topic="Bad Host Script",
        duration_minutes=10,
        language="zh",
        output_dir=Path("./output"),
    )
    planner = ThemePlanner(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"segments\[0\]\.host_script"):
        planner.generate_plan(request)


def test_v22_theme_planner_error_message_for_invalid_target_playlist_type() -> None:
    """v2.2：target_playlist 类型错误时，应抛带定位信息的 AIServiceError。"""
    payload = {
        "style_description": "bad playlist type",
        "overall_bpm_range": [90, 120],
        "segments": [
            {
                "name": "开场",
                "target_duration_seconds": 300,
                "bpm_range": [90, 100],
                "mood": "warm",
                "host_script": "欢迎收听。",
                "target_playlist": "not-a-list",
            }
        ],
    }
    request = EpisodeRequest(
        topic="Bad Playlist Type",
        duration_minutes=10,
        language="zh",
        output_dir=Path("./output"),
    )
    planner = ThemePlanner(llm_client=_StubLLMClient(payload))

    with pytest.raises(AIServiceError, match=r"segments\[0\]\.target_playlist"):
        planner.generate_plan(request)

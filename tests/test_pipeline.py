"""流水线单元测试：plan_episode 在 mock LLM 下可跑通、create_episode 结构。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_ai.core.models import EpisodePlan, EpisodeRequest, EpisodeSegment
from podcast_ai.core.pipeline import load_plan_from_disk, plan_episode, save_plan_to_disk


def _mock_plan_json() -> str:
    """返回 LLM 模拟的 EpisodePlan JSON。"""
    return json.dumps(
        {
            "style_description": "Mock style",
            "overall_bpm_range": [90, 120],
            "segments": [
                {
                    "name": "开场",
                    "target_duration_seconds": 300,
                    "bpm_range": [90, 110],
                    "mood": "chill",
                    "host_script": "欢迎。",
                    "target_playlist": [
                        {"recommended_tracks": ["Track A"], "search_hints": {"genre": "chill"}},
                    ],
                },
            ],
        },
        ensure_ascii=False,
    )


@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan")
def test_plan_episode_with_mock_llm(mock_generate: object, tmp_path: Path) -> None:
    """plan_episode 在 mock LLM 下应正常完成并落盘。"""
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Mock style",
        plan_id="mock_plan",
    )
    mock_generate.return_value = plan

    request = EpisodeRequest(
        topic="Test",
        duration_minutes=10,
        language="zh",
        output_dir=tmp_path,
    )

    result_plan, plan_path, playlist_path = plan_episode(request)

    assert result_plan.plan_id
    assert plan_path.exists()
    assert plan_path.suffix == ".json"
    assert playlist_path.exists()
    assert "Test" in playlist_path.read_text(encoding="utf-8")


def test_save_and_load_plan_roundtrip(tmp_path: Path, sample_plan: EpisodePlan) -> None:
    """save_plan_to_disk / load_plan_from_disk 往返。"""
    from podcast_ai.infra.config import AppConfig, Settings

    settings = Settings(app=AppConfig(output_dir=str(tmp_path)))
    plan_path = save_plan_to_disk(sample_plan, settings=settings, episode_id="ep_test", plan_id="plan_test")
    loaded = load_plan_from_disk(plan_path)
    assert loaded.plan_id == sample_plan.plan_id
    assert len(loaded.segments) == len(sample_plan.segments)
    assert loaded.segments[0].name == sample_plan.segments[0].name

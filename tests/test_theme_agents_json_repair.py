from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, Settings
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.state import initialize_plan_state


class _StubLLMClient:
    def __init__(self, raw_text: str) -> None:
        self._raw = raw_text

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        return self._raw


def _request(tmp_path: Path) -> EpisodeRequest:
    return EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language="zh",
        output_dir=tmp_path,
    )


def _base_settings(tmp_path: Path) -> Settings:
    # Agent 用的是 settings.app.output_dir 写 debug dump
    return Settings(app=AppConfig(output_dir=str(tmp_path)))


def test_planner_agent_json_repair_success(tmp_path: Path) -> None:
    payload = """
    一些前后文本，以及代码块：
    ```json
    {
      "meta": {"theme_description": "深夜陪伴"},
      "global_constraints": {"tone": "克制", "avoid": ["说教",]},
      "plan": {"segments_design": "两段式", "emotion_curve": ["平静", "治愈",]},
      "segments": [
        {
          "segment_id": "seg_01",
          "order": 1,
          "name": "开场",
          "target_duration_seconds": 1200,
          "bpm_range": [90, 105],
          "mood": "舒缓",
          "segment_design": "铺垫主题",
        }
      ],
    }
    ```
    """
    request = EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language="zh",
        output_dir=tmp_path,
    )
    state = initialize_plan_state(request)
    agent = PlannerAgent(llm_client=_StubLLMClient(payload), settings=_base_settings(tmp_path))
    next_state = agent.run(state)
    assert next_state["control"]["last_updated_by"] == "Planner"
    assert next_state["meta"]["theme_description"] == "深夜陪伴"


def test_music_curator_agent_json_repair_success(tmp_path: Path) -> None:
    request = _request(tmp_path)
    state = initialize_plan_state(request)
    bad = """
    ```json
    { "segments": [
      { "playlist": [
        {"track":"Track A - Artist X", "artist":"Artist X","bpm": 98,},
      ], },
    ] }
    ```
    解释性文字
    """
    agent = MusicCuratorAgent(llm_client=_StubLLMClient(bad), settings=_base_settings(tmp_path))
    next_state = agent.run(state)
    assert len(next_state["segments"][0]["playlist"]) == 1


def test_script_writer_agent_json_repair_success(tmp_path: Path) -> None:
    request = _request(tmp_path)
    state = initialize_plan_state(request)

    # script writer 要求 segment_intro 非空且语言匹配（zh：至少包含中文字符）
    bad = """
    ```json
    {
      "segments": [
        {
          "script": {
            "segment_intro": "欢迎收听今晚的 Luma Hits。",
            "between_tracks": [
              {"after_track_index": 0, "text": null,},
              {"after_track_index": 1, "text": "下一首把情绪推高。",},
            ],
          }
        }
      ],
    }
    ```
    """
    agent = ScriptWriterAgent(llm_client=_StubLLMClient(bad), settings=_base_settings(tmp_path))
    next_state = agent.run(state)
    assert next_state["segments"][0]["script"]["segment_intro"].startswith("欢迎收听")
    assert next_state["segments"][0]["script"]["between_tracks"][1]["text"].startswith("下一首把情绪")


def test_critic_agent_json_repair_success(tmp_path: Path) -> None:
    request = _request(tmp_path)
    state = initialize_plan_state(request)

    bad = """
    ```json
    {
      "critic": {
        "pass": true,
        "scores": {"coherence": 8, "emotion_flow": 8, "immersion": 8,},
        "issues": [],
        "actions": [],
      },
      "control": {"next_agent": "Planner",},
    }
    ```
    """
    agent = CriticAgent(llm_client=_StubLLMClient(bad), settings=_base_settings(tmp_path))
    next_state = agent.run(state)
    assert next_state["critic"]["pass"] is True


def test_planner_agent_json_repair_failure_raises_and_dumps_debug(tmp_path: Path) -> None:
    request = _request(tmp_path)
    state = initialize_plan_state(request)

    # repair 可能无法提取成可解析 JSON
    bad = "```json\n{not-valid:}\n```"
    agent = PlannerAgent(llm_client=_StubLLMClient(bad), settings=_base_settings(tmp_path))

    with pytest.raises(AIServiceError) as exc_info:
        agent.run(state)

    assert "Planner Agent" in str(exc_info.value)

    debug_files = list((tmp_path / "debug").glob("*jsondecode_error*"))
    assert debug_files, "json repair 失败应当写入 debug dump 文件"


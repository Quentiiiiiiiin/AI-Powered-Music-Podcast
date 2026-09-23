"""v7.1 Task 02：JSON 解析失败时仍落盘 Agent 原始返回。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings
from podcast_ai.infra.storage.paths import (
    format_audit_agent_filename,
    get_multi_agent_audit_run_dir,
)
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
from podcast_ai.modules.theme.plan_audit import (
    AUDIT_SLUG_CRITIC,
    AUDIT_SLUG_MUSIC_CURATOR,
    AUDIT_SLUG_PLANNER,
    AUDIT_SLUG_SCRIPT_WRITER,
    FilePlanAuditSink,
)
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.state import initialize_plan_state

# 故意非法、可在落盘文件中复查的原文标记
_BAD_RAW = "NOT_JSON_v71_AUDIT_MARKER {{{ broken"


class _RawStubLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        return self._text


def _settings(tmp: Path) -> Settings:
    return Settings(
        app=AppConfig(output_dir=str(tmp)),
        llm=LLMConfig(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="m",
            structured_output=False,
        ),
    )


def _episode(tmp: Path) -> EpisodeRequest:
    return EpisodeRequest(
        topic="T",
        duration_minutes=60,
        language="zh",
        output_dir=tmp,
    )


def _state_with_one_segment(tmp: Path) -> dict[str, Any]:
    state = initialize_plan_state(_episode(tmp))
    state["segments"] = [
        {
            "segment_id": "seg_01",
            "order": 1,
            "name": "开场",
            "target_duration_seconds": 600,
            "narrative_function": "x",
            "scene": "night",
            "sonic_direction": [],
            "lyrical_direction": [],
            "anchor_tracks": [],
            "reference_material": [],
            "sequence_direction": [],
            "transition_to_next": "",
            "playlist": [],
            "script": "",
        }
    ]
    return state


def _assert_parse_fail_audit(
    *,
    run_dir: Path,
    iteration: int,
    slug: str,
    rid: str,
) -> None:
    path = run_dir / format_audit_agent_filename(iteration, slug)
    assert path.is_file(), f"期望审计文件存在：{path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["iteration"] == iteration
    assert data["agent_slug"] == slug
    assert data["request_id"] == rid
    assert _BAD_RAW in data["raw_llm_text"]
    assert data["parsed_patch"] is None


@pytest.mark.parametrize(
    "agent_factory,slug,need_segments",
    [
        (lambda llm, s: PlannerAgent(llm_client=llm, settings=s), AUDIT_SLUG_PLANNER, False),
        (lambda llm, s: MusicCuratorAgent(llm_client=llm, settings=s), AUDIT_SLUG_MUSIC_CURATOR, True),
        (lambda llm, s: ScriptWriterAgent(llm_client=llm, settings=s), AUDIT_SLUG_SCRIPT_WRITER, True),
        (lambda llm, s: CriticAgent(llm_client=llm, settings=s), AUDIT_SLUG_CRITIC, False),
    ],
)
def test_parse_failure_still_writes_raw_audit(
    tmp_path: Path,
    agent_factory: Any,
    slug: str,
    need_segments: bool,
) -> None:
    state = _state_with_one_segment(tmp_path) if need_segments else initialize_plan_state(_episode(tmp_path))
    rid = str(state["meta"]["request_id"])
    run_dir = get_multi_agent_audit_run_dir(tmp_path, rid)
    sink = FilePlanAuditSink(run_dir)
    agent = agent_factory(_RawStubLLM(_BAD_RAW), _settings(tmp_path))

    with pytest.raises(AIServiceError, match=r"JSON|解析"):
        agent.run(state, audit_sink=sink, round_iteration=3)

    _assert_parse_fail_audit(run_dir=run_dir, iteration=3, slug=slug, rid=rid)


def test_parse_failure_still_raises_after_audit_write(tmp_path: Path) -> None:
    """审计写成功后仍须抛出原解析错误；原文可复查。"""
    state = initialize_plan_state(_episode(tmp_path))
    rid = str(state["meta"]["request_id"])
    run_dir = tmp_path / "audit"
    sink = FilePlanAuditSink(run_dir)
    agent = PlannerAgent(llm_client=_RawStubLLM(_BAD_RAW), settings=_settings(tmp_path))
    with pytest.raises(AIServiceError, match=r"JSON|解析"):
        agent.run(state, audit_sink=sink, round_iteration=1)
    path = run_dir / format_audit_agent_filename(1, AUDIT_SLUG_PLANNER)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["request_id"] == rid
    assert _BAD_RAW in data["raw_llm_text"]
    assert data["parsed_patch"] is None

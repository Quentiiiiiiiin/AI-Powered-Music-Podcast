"""v3.6：multi-agent 审计落盘路径、轮次对齐、写盘失败不拖垮主流程。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings
from podcast_ai.infra.storage.paths import (
    format_audit_agent_filename,
    format_audit_state_filename,
    format_audit_state_partial_filename,
    get_multi_agent_audit_run_dir,
)
from podcast_ai.modules.theme.orchestrator import PlanOrchestrator
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.plan_audit import FilePlanAuditSink
from podcast_ai.modules.theme.state import initialize_plan_state

# 复用 test_theme_orchestrator 的 stub 套件
from test_theme_orchestrator import (
    _CriticStubOncePass,
    _CriticStubSecondPass,
    _CuratorStub,
    _PlannerStub,
    _WriterStub,
)


def test_audit_path_and_filenames() -> None:
    base = Path("/tmp/out")
    rid = "req-abc"
    assert get_multi_agent_audit_run_dir(base, rid) == Path("/tmp/out") / "audit" / "multi_agent" / "req-abc"
    assert format_audit_agent_filename(3, "planner") == "iteration3_planner.json"
    assert format_audit_state_filename(2) == "iteration2_state.json"
    assert format_audit_state_partial_filename(1) == "iteration1_state_partial.json"


class _StubLLMClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def _episode_request(tmp: Path) -> EpisodeRequest:
    return EpisodeRequest(
        topic="T",
        duration_minutes=60,
        language="zh",
        output_dir=tmp,
    )


def _planner_payload_min() -> dict[str, Any]:
    return {
        "meta": {"theme_description": "x"},
        "global_constraints": None,
        "plan": None,
        "segments": None,
    }


def test_planner_writes_iteration_agent_audit(tmp_path: Path) -> None:
    state = initialize_plan_state(_episode_request(tmp_path))
    rid = str(state["meta"]["request_id"])
    run_dir = get_multi_agent_audit_run_dir(tmp_path, rid)
    sink = FilePlanAuditSink(run_dir)
    llm = LLMConfig(
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="m",
        structured_output=False,
    )
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm)
    agent = PlannerAgent(llm_client=_StubLLMClient(_planner_payload_min()), settings=settings)
    agent.run(state, audit_sink=sink, round_iteration=7)
    path = run_dir / "iteration7_planner.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["iteration"] == 7
    assert data["agent_slug"] == "planner"
    assert data["mode"] == "generation"
    assert data["request_id"] == rid
    assert "parsed_patch" in data


def test_orchestrator_writes_iteration_state_per_round(tmp_path: Path) -> None:
    state = initialize_plan_state(_episode_request(tmp_path))
    orch = PlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=_CriticStubSecondPass(),
        settings=Settings(app=AppConfig(output_dir=str(tmp_path), multi_agent_audit_enabled=True)),
    )
    orch.run(_episode_request(tmp_path), initial_state=state)
    rid = str(state["meta"]["request_id"])
    run_dir = get_multi_agent_audit_run_dir(tmp_path, rid)
    s1 = run_dir / "iteration1_state.json"
    s2 = run_dir / "iteration2_state.json"
    assert s1.is_file()
    assert s2.is_file()
    doc1 = json.loads(s1.read_text(encoding="utf-8"))
    doc2 = json.loads(s2.read_text(encoding="utf-8"))
    assert doc1["meta"]["request_id"] == rid
    assert doc2["meta"]["request_id"] == rid


def test_audit_write_oserror_does_not_fail_planner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = initialize_plan_state(_episode_request(tmp_path))
    rid = str(state["meta"]["request_id"])
    run_dir = get_multi_agent_audit_run_dir(tmp_path, rid)
    sink = FilePlanAuditSink(run_dir)
    llm = LLMConfig(
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="m",
        structured_output=False,
    )
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm)
    agent = PlannerAgent(llm_client=_StubLLMClient(_planner_payload_min()), settings=settings)

    _orig = Path.write_text

    def _patched_write_text(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "iteration1_planner.json":
            raise OSError("mock disk full")
        return _orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text)
    next_state = agent.run(state, audit_sink=sink, round_iteration=1)
    assert next_state["meta"]["theme_description"] == "x"


def test_orchestrator_audit_disabled_no_audit_dir(tmp_path: Path) -> None:
    state = initialize_plan_state(_episode_request(tmp_path))
    orch = PlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=_CriticStubOncePass(),
        settings=Settings(app=AppConfig(output_dir=str(tmp_path), multi_agent_audit_enabled=False)),
    )
    orch.run(_episode_request(tmp_path), initial_state=state)
    rid = str(state["meta"]["request_id"])
    run_dir = get_multi_agent_audit_run_dir(tmp_path, rid)
    assert not run_dir.exists()

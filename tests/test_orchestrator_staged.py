"""v6.0：StagedPlanOrchestrator 闸门 FSM 与 dual-mode 回归。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, Settings
from podcast_ai.infra.storage.paths import build_episode_snapshot_from_state
from podcast_ai.modules.theme.critic_agent import CriticAgent, _sanitize_critic_patch_staged
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
from podcast_ai.modules.theme.orchestrator import PlanOrchestrator
from podcast_ai.modules.theme.orchestrator_staged import StagedPlanOrchestrator
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.state import PlanState, initialize_plan_state, merge_plan_state


class _TraceAgentBase:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    def _append_trace(self, state: PlanState) -> PlanState:
        trace = list(state.get("meta", {}).get("generation_trace") or [])
        trace.append(self.name)
        return merge_plan_state(state, {"meta": {"generation_trace": trace}})


class _PlannerStub(_TraceAgentBase, PlannerAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Planner")

    def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
        self.calls.append({"mode": mode, **{k: kwargs.get(k) for k in ("orchestration_mode", "audit_stage", "audit_revision")}})
        state = self._append_trace(state)
        return merge_plan_state(state, {"control": {"last_updated_by": "Planner"}})


class _CuratorStub(_TraceAgentBase, MusicCuratorAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Music Curator")

    def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
        self.calls.append({"mode": mode, **{k: kwargs.get(k) for k in ("orchestration_mode", "audit_stage", "audit_revision")}})
        state = self._append_trace(state)
        return merge_plan_state(state, {"control": {"last_updated_by": "Music Curator"}})


class _WriterStub(_TraceAgentBase, ScriptWriterAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Script Writer")

    def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
        self.calls.append({"mode": mode, **{k: kwargs.get(k) for k in ("orchestration_mode", "audit_stage", "audit_revision")}})
        state = self._append_trace(state)
        return merge_plan_state(state, {"control": {"last_updated_by": "Script Writer"}})


class _CriticAlwaysPass(_TraceAgentBase, CriticAgent):
    """staged：仅写 critic，不写 control.next_agent。"""

    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Critic")

    def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
        self.calls.append({"mode": mode, "stage": kwargs.get("stage"), "orchestration_mode": kwargs.get("orchestration_mode")})
        state = self._append_trace(state)
        return merge_plan_state(
            state,
            {
                "critic": {
                    "pass": True,
                    "scores": {"coherence": 8, "emotion_flow": 8, "immersion": 8},
                    "issues": [],
                    "actions": [],
                },
            },
        )


class _CriticPassAfterN(_TraceAgentBase, CriticAgent):
    """同一阶段内前 fail_times 次 fail，之后 pass。跨阶段独立计数由外部重置。"""

    def __init__(self, *, fail_times: int) -> None:
        _TraceAgentBase.__init__(self, "Critic")
        self._fail_times = fail_times
        self._by_stage: dict[str, int] = {}

    def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
        stage = str(kwargs.get("stage") or "unknown")
        self._by_stage[stage] = self._by_stage.get(stage, 0) + 1
        n = self._by_stage[stage]
        state = self._append_trace(state)
        passed = n > self._fail_times
        return merge_plan_state(
            state,
            {
                "critic": {
                    "pass": passed,
                    "scores": {"coherence": 8 if passed else 4, "emotion_flow": 8 if passed else 4, "immersion": 8 if passed else 4},
                    "issues": [] if passed else [{"type": "coherence", "location": "plan", "problem": "x", "suggestion": "y"}],
                    "actions": [] if passed else [{"target_agent": "Planner", "instruction": "fix"}],
                },
            },
        )


class _CriticNeverPass(_TraceAgentBase, CriticAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Critic")

    def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
        self.calls.append({"stage": kwargs.get("stage"), "revision": kwargs.get("audit_revision")})
        state = self._append_trace(state)
        return merge_plan_state(
            state,
            {
                "critic": {
                    "pass": False,
                    "scores": {"coherence": 3, "emotion_flow": 3, "immersion": 3},
                    "issues": [],
                    "actions": [{"target_agent": "Planner", "instruction": "never"}],
                },
            },
        )


def _request() -> EpisodeRequest:
    return EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language="zh",
        output_dir=Path("./output"),
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(app=AppConfig(output_dir=str(tmp_path), multi_agent_audit_enabled=False))


def test_app_orchestration_mode_default_is_staged() -> None:
    assert AppConfig().orchestration_mode == "staged"


def test_app_orchestration_mode_rejects_invalid() -> None:
    with pytest.raises(Exception):
        AppConfig(orchestration_mode="foobar")  # type: ignore[arg-type]


def test_staged_happy_path_order_and_completed(tmp_path: Path) -> None:
    planner, curator, writer = _PlannerStub(), _CuratorStub(), _WriterStub()
    orch = StagedPlanOrchestrator(
        planner=planner,
        curator=curator,
        writer=writer,
        critic=_CriticAlwaysPass(),
        settings=_settings(tmp_path),
    )
    final_state = orch.run(_request(), initial_state=initialize_plan_state(_request()))
    assert final_state["meta"]["generation_trace"] == [
        "Planner",
        "Critic",
        "Music Curator",
        "Critic",
        "Script Writer",
        "Critic",
    ]
    assert final_state["control"]["status"] == "completed"
    assert final_state["control"].get("failed_stage") is None
    assert planner.calls[0]["orchestration_mode"] == "staged"
    assert writer.calls[0]["audit_stage"] == "script_writer"


def test_staged_revision_budget_max_three_critic_then_fail_no_downstream(tmp_path: Path) -> None:
    """planner 永远不通过：最多 3 次 Critic；不调用 Curator/Writer。"""
    planner, curator, writer = _PlannerStub(), _CuratorStub(), _WriterStub()
    critic = _CriticNeverPass()
    orch = StagedPlanOrchestrator(
        planner=planner,
        curator=curator,
        writer=writer,
        critic=critic,
        settings=_settings(tmp_path),
    )
    final_state = orch.run(_request(), initial_state=initialize_plan_state(_request()))
    assert len(planner.calls) == 3
    assert planner.calls[0]["mode"] == "generation"
    assert planner.calls[1]["mode"] == "revision"
    assert planner.calls[2]["mode"] == "revision"
    assert curator.calls == []
    assert writer.calls == []
    assert len(critic.calls) == 3
    assert final_state["control"]["status"] == "stage_failed"
    assert final_state["control"]["failed_stage"] == "planner"


def test_staged_fail_mid_stage_no_rollback_to_upstream(tmp_path: Path) -> None:
    """Curator 失败：Planner 已跑过且不再重跑；Writer 不调用。"""
    planner, curator, writer = _PlannerStub(), _CuratorStub(), _WriterStub()

    class _CriticFailCuratorOnly(_TraceAgentBase, CriticAgent):
        def __init__(self) -> None:
            _TraceAgentBase.__init__(self, "Critic")

        def run(self, state: PlanState, mode: str = "generation", **kwargs: Any) -> PlanState:  # type: ignore[override]
            stage = kwargs.get("stage")
            state = self._append_trace(state)
            passed = stage != "music_curator"
            return merge_plan_state(
                state,
                {
                    "critic": {
                        "pass": passed,
                        "scores": {"coherence": 8 if passed else 2, "emotion_flow": 8 if passed else 2, "immersion": 8 if passed else 2},
                        "issues": [],
                        "actions": [],
                    },
                },
            )

    orch = StagedPlanOrchestrator(
        planner=planner,
        curator=curator,
        writer=writer,
        critic=_CriticFailCuratorOnly(),
        settings=_settings(tmp_path),
    )
    final_state = orch.run(_request(), initial_state=initialize_plan_state(_request()))
    assert len(planner.calls) == 1
    assert len(curator.calls) == 3
    assert writer.calls == []
    assert final_state["control"]["status"] == "stage_failed"
    assert final_state["control"]["failed_stage"] == "music_curator"


def test_staged_pass_after_one_revision(tmp_path: Path) -> None:
    """每阶段第一次 Critic fail，第二次 pass（共 2 次 Critic / 阶段）。"""
    planner, curator, writer = _PlannerStub(), _CuratorStub(), _WriterStub()
    orch = StagedPlanOrchestrator(
        planner=planner,
        curator=curator,
        writer=writer,
        critic=_CriticPassAfterN(fail_times=1),
        settings=_settings(tmp_path),
    )
    final_state = orch.run(_request(), initial_state=initialize_plan_state(_request()))
    assert final_state["control"]["status"] == "completed"
    assert len(planner.calls) == 2
    assert len(curator.calls) == 2
    assert len(writer.calls) == 2


def test_staged_critic_sanitize_rejects_next_agent() -> None:
    with pytest.raises(Exception) as ei:
        _sanitize_critic_patch_staged(
            {
                "critic": {
                    "pass": True,
                    "scores": {"coherence": 8, "emotion_flow": 8, "immersion": 8},
                    "threshold": {"coherence": 28, "emotion_flow": 28, "immersion": 24},
                    "issues": [],
                    "actions": [],
                },
                "control": {"next_agent": "Planner"},
            }
        )
    assert "control" in str(ei.value).lower() or "next_agent" in str(ei.value).lower()


def test_staged_failed_state_still_builds_snapshot(tmp_path: Path) -> None:
    orch = StagedPlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=_CriticNeverPass(),
        settings=_settings(tmp_path),
    )
    final_state = orch.run(_request(), initial_state=initialize_plan_state(_request()))
    snap = build_episode_snapshot_from_state(final_state)
    assert "segments" in snap and len(snap["segments"]) >= 1
    assert "playlists" in snap["segments"][0]
    assert "script" in snap["segments"][0]


def test_staged_on_progress_emits_stage_revision(tmp_path: Path) -> None:
    events: list[dict] = []
    orch = StagedPlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=_CriticAlwaysPass(),
        settings=_settings(tmp_path),
    )
    orch.run(_request(), initial_state=initialize_plan_state(_request()), on_progress=events.append)
    assert any(e.get("event") == "stage_start" and e.get("stage") == "planner" for e in events)
    assert any(e.get("event") == "completed" for e in events)


def test_legacy_orchestrator_still_converges(tmp_path: Path) -> None:
    """legacy PlanOrchestrator 冒烟：与 v3 单轮语义一致。"""
    from test_theme_orchestrator import (
        _CriticStubOncePass,
        _CuratorStub as LCurator,
        _PlannerStub as LPlanner,
        _WriterStub as LWriter,
        _request as lreq,
    )

    state = initialize_plan_state(lreq())
    orch = PlanOrchestrator(
        planner=LPlanner(),
        curator=LCurator(),
        writer=LWriter(),
        critic=_CriticStubOncePass(),
        settings=_settings(tmp_path),
    )
    final_state = orch.run(lreq(), initial_state=state)
    assert final_state["control"]["status"] == "completed"


def test_theme_planner_dispatches_staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from podcast_ai.modules.theme.llm_planner import ThemePlanner

    called: dict[str, Any] = {}

    class _FakeStaged:
        def __init__(self, **kwargs: Any) -> None:
            called["ctor"] = kwargs

        def run(self, request: EpisodeRequest, **kwargs: Any) -> PlanState:
            called["run"] = True
            return initialize_plan_state(request)

    monkeypatch.setattr(
        "podcast_ai.modules.theme.orchestrator_staged.StagedPlanOrchestrator",
        _FakeStaged,
    )
    settings = Settings(app=AppConfig(output_dir=str(tmp_path), orchestration_mode="staged", multi_agent_audit_enabled=False))
    ThemePlanner(settings=settings).generate_plan_state(_request(), agent_mode="multi_agent")
    assert called.get("run") is True


def test_theme_planner_dispatches_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from podcast_ai.modules.theme.llm_planner import ThemePlanner

    called: dict[str, Any] = {}

    class _FakeLegacy:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, request: EpisodeRequest, **kwargs: Any) -> PlanState:
            called["legacy"] = True
            return initialize_plan_state(request)

    monkeypatch.setattr(
        "podcast_ai.modules.theme.orchestrator.PlanOrchestrator",
        _FakeLegacy,
    )
    settings = Settings(app=AppConfig(output_dir=str(tmp_path), orchestration_mode="legacy", multi_agent_audit_enabled=False))
    ThemePlanner(settings=settings).generate_plan_state(_request(), agent_mode="multi_agent")
    assert called.get("legacy") is True

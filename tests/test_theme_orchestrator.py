"""v3.0：PlanOrchestrator 多 Agent 编排与迭代控制测试。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.orchestrator import PlanOrchestrator
from podcast_ai.modules.theme.state import PlanState, initialize_plan_state, merge_plan_state


class _TraceAgentBase:
    def __init__(self, name: str) -> None:
        self._name = name

    def _append_trace(self, state: PlanState) -> PlanState:
        trace = list(state.get("meta", {}).get("generation_trace") or [])
        trace.append(self._name)
        return merge_plan_state(state, {"meta": {"generation_trace": trace}})


class _PlannerStub(_TraceAgentBase, PlannerAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Planner")

    def run(self, state: PlanState, mode: str = "generation") -> PlanState:  # type: ignore[override]
        state = self._append_trace(state)
        return merge_plan_state(state, {"control": {"last_updated_by": "Planner"}})


class _CuratorStub(_TraceAgentBase, MusicCuratorAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Music Curator")

    def run(self, state: PlanState, mode: str = "generation") -> PlanState:  # type: ignore[override]
        state = self._append_trace(state)
        return merge_plan_state(state, {"control": {"last_updated_by": "Music Curator"}})


class _WriterStub(_TraceAgentBase, ScriptWriterAgent):
    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Script Writer")

    def run(self, state: PlanState, mode: str = "generation") -> PlanState:  # type: ignore[override]
        state = self._append_trace(state)
        return merge_plan_state(state, {"control": {"last_updated_by": "Script Writer"}})


class _CriticStubOncePass(_TraceAgentBase, CriticAgent):
    """第一次评估即 pass=true，用于测试「单轮收敛」路径。"""

    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Critic")

    def run(self, state: PlanState) -> PlanState:  # type: ignore[override]
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
                "control": {"next_agent": "Planner"},
            },
        )


class _CriticStubSecondPass(_TraceAgentBase, CriticAgent):
    """第一次不通过（回修 Planner），第二次通过。"""

    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Critic")
        self._call_count = 0

    def run(self, state: PlanState) -> PlanState:  # type: ignore[override]
        self._call_count += 1
        state = self._append_trace(state)
        if self._call_count == 1:
            # 第一次：不通过，要求回到 Planner
            return merge_plan_state(
                state,
                {
                    "critic": {
                        "pass": False,
                        "scores": {"coherence": 5, "emotion_flow": 5, "immersion": 6},
                        "issues": [
                            {
                                "type": "emotion_flow",
                                "location": "segments[0].playlist[0]",
                                "problem": "情绪跳跃过大",
                                "suggestion": "调整第一段选曲。",
                            }
                        ],
                        "actions": [
                            {
                                "target_agent": "Planner",
                                "instruction": "重新设计情绪曲线并调整段落时长分配。",
                            }
                        ],
                    },
                    "control": {"next_agent": "Planner"},
                },
            )
        # 第二次：通过
        return merge_plan_state(
            state,
            {
                "critic": {
                    "pass": True,
                    "scores": {"coherence": 8, "emotion_flow": 8, "immersion": 8},
                    "issues": [],
                    "actions": [],
                },
                "control": {"next_agent": "Planner"},
            },
        )


class _CriticStubNeverPass(_TraceAgentBase, CriticAgent):
    """永远 pass=false，用于测试 max_iterations。"""

    def __init__(self) -> None:
        _TraceAgentBase.__init__(self, "Critic")

    def run(self, state: PlanState) -> PlanState:  # type: ignore[override]
        state = self._append_trace(state)
        critic = {
            "pass": False,
            "scores": {"coherence": 5, "emotion_flow": 5, "immersion": 5},
            "issues": [],
            "actions": [
                {
                    "target_agent": "Planner",
                    "instruction": "尝试改进整体结构，但始终不满足阈值（测试用）。",
                }
            ],
        }
        return merge_plan_state(state, {"critic": critic, "control": {"next_agent": "Planner"}})


def _request() -> EpisodeRequest:
    return EpisodeRequest(
        topic="Late Night Chill",
        duration_minutes=60,
        language="zh",
        output_dir=Path("./output"),
    )


def test_v30_orchestrator_single_round_completed() -> None:
    """正常路径：一轮 Planner→Curator→Writer→Critic 即通过，status=completed。"""
    state = initialize_plan_state(_request())
    orch = PlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=_CriticStubOncePass(),
    )

    final_state = orch.run(_request(), initial_state=state)
    trace = final_state["meta"].get("generation_trace")
    assert trace == ["Planner", "Music Curator", "Script Writer", "Critic"]
    assert final_state["control"]["status"] == "completed"
    # 初始 iteration=1，完成一轮后应为 2
    assert final_state["control"]["iteration"] == 2


def test_v30_orchestrator_refinement_rounds_using_next_agent() -> None:
    """
    pass=false 路径：第一次 Critic 不通过并设置 next_agent=Planner，第二轮从 Planner 重新开始，第二次 Critic 通过。
    """
    state = initialize_plan_state(_request())
    critic = _CriticStubSecondPass()
    orch = PlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=critic,
    )

    final_state = orch.run(_request(), initial_state=state)
    trace = final_state["meta"].get("generation_trace")
    # 两轮：Planner, Curator, Writer, Critic, Planner, Curator, Writer, Critic
    assert trace == [
        "Planner",
        "Music Curator",
        "Script Writer",
        "Critic",
        "Planner",
        "Music Curator",
        "Script Writer",
        "Critic",
    ]
    assert final_state["control"]["status"] == "completed"
    # 初始 iteration=1，经历两轮后应为 3
    assert final_state["control"]["iteration"] == 3


def test_v30_orchestrator_stops_at_max_iterations() -> None:
    """超过 max_iterations：永远 pass=false，最终 status=max_iterations_reached。"""
    state = initialize_plan_state(_request())
    # 设置 max_iterations 为 2，方便断言
    state = merge_plan_state(state, {"control": {"max_iterations": 2}})

    orch = PlanOrchestrator(
        planner=_PlannerStub(),
        curator=_CuratorStub(),
        writer=_WriterStub(),
        critic=_CriticStubNeverPass(),
    )

    final_state = orch.run(_request(), initial_state=state)
    trace = final_state["meta"].get("generation_trace")
    # 两轮，每轮都会跑到 Critic
    assert trace == [
        "Planner",
        "Music Curator",
        "Script Writer",
        "Critic",
        "Planner",
        "Music Curator",
        "Script Writer",
        "Critic",
    ]
    assert final_state["control"]["status"] == "max_iterations_reached"
    assert final_state["control"]["iteration"] == 3


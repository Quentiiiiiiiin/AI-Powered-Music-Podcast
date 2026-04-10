from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.storage.paths import get_multi_agent_audit_run_dir
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
from podcast_ai.modules.theme.plan_audit import FilePlanAuditSink, PlanAuditSink
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.state import PlanState, assert_plan_state_valid, initialize_plan_state, merge_plan_state

logger = logging.getLogger(__name__)

_AGENT_ORDER: List[str] = ["Planner", "Music Curator", "Script Writer", "Critic"]


class PlanOrchestrator:
    """
    v3.0 多 Agent 编排器。

    语义：
    - 「一轮 iteration」= 从某个 Agent 起步，按顺序至少执行到 Critic（Planner → Curator → Writer → Critic 的子序列）
    - `control.max_iterations`：最多允许运行多少轮 Critic 评估（即最多几次回修循环）
    - Critic.pass = true：视为收敛，`control.status = "completed"`，提前结束
    - 达到 max_iterations 仍未通过：`control.status = "max_iterations_reached"`
    - 任一 Agent 抛 AIServiceError：`control.status = "error"` 并记录 last_error_* 字段
    """

    def __init__(
        self,
        *,
        planner: Optional[PlannerAgent] = None,
        curator: Optional[MusicCuratorAgent] = None,
        writer: Optional[ScriptWriterAgent] = None,
        critic: Optional[CriticAgent] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        # 若未显式注入，则按默认配置初始化各 Agent
        self._planner = planner or PlannerAgent(settings=self._settings)
        self._curator = curator or MusicCuratorAgent(settings=self._settings)
        self._writer = writer or ScriptWriterAgent(settings=self._settings)
        self._critic = critic or CriticAgent(settings=self._settings)

    def run(self, request: EpisodeRequest, *, initial_state: PlanState | None = None) -> PlanState:
        """
        在 max_iterations 限制下，执行多轮「至少到 Critic」的 pipeline，返回最终 PlanState。
        """
        state = initial_state or initialize_plan_state(request)
        assert_plan_state_valid(state)

        audit_sink: PlanAuditSink | None = None
        if self._settings.app.multi_agent_audit_enabled:
            rid = str(state.get("meta", {}).get("request_id") or "unknown")
            audit_sink = FilePlanAuditSink(
                get_multi_agent_audit_run_dir(Path(self._settings.app.output_dir), rid),
            )

        control = state.get("control", {})
        max_iterations = int(control.get("max_iterations", 3) or 3)
        iteration = int(control.get("iteration", 1) or 1)
        mode = str("generation")

        # 外层循环：一轮 = 至少执行到 Critic 一次
        while iteration <= max_iterations:
            next_agent = str(state.get("control", {}).get("next_agent") or "Planner")
            logger.info("PlanOrchestrator iteration=%d, start_agent=%s", iteration, next_agent)

            state_at_round_start = deepcopy(state)
            try:
                state = self._run_round_from(
                    next_agent,
                    state,
                    mode,
                    audit_sink=audit_sink,
                    round_iteration=iteration,
                )
                assert_plan_state_valid(state)
                if audit_sink is not None:
                    # 与进入本轮 while 时的 iteration（业务轮次 i）对齐，勿用 Critic 通过后 merge 的 control.iteration
                    audit_sink.write_state_snapshot(round_iteration=iteration, state=state)
            except AIServiceError as exc:
                logger.error("PlanOrchestrator 在 iteration=%d 执行 %s 轮次时发生 AIServiceError：%s", iteration, next_agent, exc)
                if audit_sink is not None:
                    audit_sink.write_state_partial(
                        round_iteration=iteration,
                        state_before_round=state_at_round_start,
                        error_message=str(exc),
                    )
                state = merge_plan_state(
                    state,
                    {
                        "control": {
                            "status": "error",
                        },
                    },
                )
                break

            critic_block = state.get("critic") or {}
            if critic_block.get("pass") is True:
                # 当前轮次完成，记录最终 iteration（本轮编号 + 1）并标记 completed
                state = merge_plan_state(
                    state,
                    {
                        "control": {
                            "status": "completed",
                            "iteration": iteration + 1,
                        },
                    },
                )
                break

            # 未通过：增加 iteration，准备下一轮（下一轮起点由 Critic 写入的 control.next_agent 决定）
            iteration += 1
            mode = str("revision")
            state = merge_plan_state(state, {"control": {"iteration": iteration}})
            assert_plan_state_valid(state)

        else:
            # while 正常结束（iteration > max_iterations）且未提前 break
            state = merge_plan_state(
                state,
                {
                    "control": {
                        "status": "max_iterations_reached",
                        "iteration": max_iterations + 1,
                    },
                },
            )

        return state

    def _run_round_from(
        self,
        start_agent: str,
        state: PlanState,
        mode: str,
        *,
        audit_sink: PlanAuditSink | None = None,
        round_iteration: int,
    ) -> PlanState:
        """
        从 start_agent 起步，按顺序执行到 Critic（含），返回更新后的 state。
        """
        if start_agent not in _AGENT_ORDER:
            raise AIServiceError(f"未知的 control.next_agent：{start_agent!r}")

        start_idx = _AGENT_ORDER.index(start_agent)
        for agent_name in _AGENT_ORDER[start_idx:]:
            logger.debug("PlanOrchestrator round: running agent=%s", agent_name)
            if agent_name == "Planner":
                state = self._planner.run(
                    state,
                    mode,
                    audit_sink=audit_sink,
                    round_iteration=round_iteration,
                )
            elif agent_name == "Music Curator":
                state = self._curator.run(
                    state,
                    mode,
                    audit_sink=audit_sink,
                    round_iteration=round_iteration,
                )
            elif agent_name == "Script Writer":
                state = self._writer.run(
                    state,
                    mode,
                    audit_sink=audit_sink,
                    round_iteration=round_iteration,
                )
            elif agent_name == "Critic":
                state = self._critic.run(
                    state,
                    mode,
                    audit_sink=audit_sink,
                    round_iteration=round_iteration,
                )
            else:
                raise AIServiceError(f"未知的 Agent：{agent_name!r}")

        return state



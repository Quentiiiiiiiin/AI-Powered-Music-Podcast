"""
v6.0 Stage-Gated Orchestrator（默认 multi_agent 路径）。

闸门顺序（Orchestrator FSM 推进；Critic 不写 next_agent）：
  Planner ⇄ Critic → Music Curator ⇄ Critic → Script Writer ⇄ Critic

每阶段：generation + 最多 2 次 revision（合计最多 3 次 Critic）；失败禁止回退上游。
失败时仍返回可用 PlanState（供上层写 snapshot），control.status / failed_stage 可追溯。

legacy 路径见 orchestrator.py（冻结）。
"""
from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Callable, Literal, Optional

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

ProgressCallback = Callable[[dict], None]

# 每阶段最多 2 次修复 → revision ∈ {0,1,2}（0=首次生成）
_MAX_REVISION = 2

StageSlug = Literal["planner", "music_curator", "script_writer"]

_STAGES: tuple[tuple[StageSlug, str], ...] = (
    ("planner", "Planner"),
    ("music_curator", "Music Curator"),
    ("script_writer", "Script Writer"),
)


def _ensure_segment_snapshot_defaults(state: PlanState) -> PlanState:
    """保证 segments 具备 playlist/script，便于失败路径仍可构建 snapshot。"""
    segs = state.get("segments")
    if not isinstance(segs, list):
        return state
    patched = False
    new_segs = []
    for seg in segs:
        if not isinstance(seg, dict):
            new_segs.append(seg)
            continue
        s = dict(seg)
        if "playlist" not in s:
            s["playlist"] = []
            patched = True
        if "script" not in s or not isinstance(s.get("script"), dict):
            s["script"] = {"segment_intro": "", "between_tracks": []}
            patched = True
        else:
            script = dict(s["script"])
            if "segment_intro" not in script:
                script["segment_intro"] = ""
                patched = True
            if "between_tracks" not in script:
                script["between_tracks"] = []
                patched = True
            s["script"] = script
        new_segs.append(s)
    if not patched:
        return state
    return merge_plan_state(state, {"segments": new_segs})


class StagedPlanOrchestrator:
    """v6.0 分阶段闸门编排器。"""

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
        self._planner = planner or PlannerAgent(settings=self._settings)
        self._curator = curator or MusicCuratorAgent(settings=self._settings)
        self._writer = writer or ScriptWriterAgent(settings=self._settings)
        self._critic = critic or CriticAgent(settings=self._settings)

    def run(
        self,
        request: EpisodeRequest,
        *,
        initial_state: PlanState | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> PlanState:
        state = initial_state or initialize_plan_state(request)
        assert_plan_state_valid(state)

        def _emit(payload: dict) -> None:
            if on_progress is None:
                return
            try:
                on_progress(payload)
            except Exception:  # noqa: BLE001
                logger.debug("on_progress 回调异常已忽略", exc_info=True)

        audit_sink: PlanAuditSink | None = None
        if self._settings.app.multi_agent_audit_enabled:
            rid = str(state.get("meta", {}).get("request_id") or "unknown")
            audit_sink = FilePlanAuditSink(
                get_multi_agent_audit_run_dir(Path(self._settings.app.output_dir), rid),
            )

        # 全局单调计数，兼容 Agent audit 的 iteration 字段
        global_step = 0

        for stage_slug, agent_label in _STAGES:
            logger.info("StagedOrchestrator stage=%s start", stage_slug)
            _emit({"event": "stage_start", "stage": stage_slug, "agent": agent_label, "status": "running"})

            stage_passed = False
            for revision in range(0, _MAX_REVISION + 1):
                mode = "generation" if revision == 0 else "revision"
                global_step += 1
                _emit(
                    {
                        "event": "agent_start",
                        "stage": stage_slug,
                        "revision": revision,
                        "agent": agent_label,
                        "status": "running",
                    }
                )
                state_before = deepcopy(state)
                try:
                    state = self._run_creator(
                        stage_slug,
                        state,
                        mode=mode,
                        audit_sink=audit_sink,
                        round_iteration=global_step,
                        revision=revision,
                    )
                    _emit(
                        {
                            "event": "agent_start",
                            "stage": stage_slug,
                            "revision": revision,
                            "agent": "Critic",
                            "status": "running",
                        }
                    )
                    state = self._critic.run(
                        state,
                        mode=mode,
                        audit_sink=audit_sink,
                        round_iteration=global_step,
                        orchestration_mode="staged",
                        stage=stage_slug,
                        audit_stage=stage_slug,
                        audit_revision=revision,
                    )
                    assert_plan_state_valid(state)
                    if audit_sink is not None:
                        audit_sink.write_state_snapshot(
                            round_iteration=global_step,
                            state=state,
                            stage=stage_slug,
                            revision=revision,
                        )
                except AIServiceError as exc:
                    logger.error(
                        "StagedOrchestrator stage=%s rev=%d AIServiceError: %s",
                        stage_slug,
                        revision,
                        exc,
                    )
                    _emit(
                        {
                            "event": "error",
                            "stage": stage_slug,
                            "revision": revision,
                            "agent": agent_label,
                            "error": str(exc),
                            "status": "error",
                        }
                    )
                    if audit_sink is not None:
                        audit_sink.write_state_partial(
                            round_iteration=global_step,
                            state_before_round=state_before,
                            error_message=str(exc),
                            stage=stage_slug,
                            revision=revision,
                        )
                    state = merge_plan_state(
                        state_before,
                        {
                            "control": {
                                "status": "error",
                                "failed_stage": stage_slug,
                                "last_updated_by": "StagedOrchestrator",
                            },
                        },
                    )
                    return _ensure_segment_snapshot_defaults(state)

                critic_block = state.get("critic") or {}
                if critic_block.get("pass") is True:
                    stage_passed = True
                    _emit(
                        {
                            "event": "stage_pass",
                            "stage": stage_slug,
                            "revision": revision,
                            "agent": "Critic",
                            "status": "passed",
                        }
                    )
                    break

                logger.info(
                    "StagedOrchestrator stage=%s rev=%d critic.pass=false",
                    stage_slug,
                    revision,
                )
                _emit(
                    {
                        "event": "stage_reject",
                        "stage": stage_slug,
                        "revision": revision,
                        "agent": "Critic",
                        "status": "rejected",
                    }
                )

            if not stage_passed:
                # 超预算：阶段失败，禁止回退；仍返回 state 供落盘
                logger.warning("StagedOrchestrator stage=%s FAILED after max revisions", stage_slug)
                state = merge_plan_state(
                    state,
                    {
                        "control": {
                            "status": "stage_failed",
                            "failed_stage": stage_slug,
                            "last_updated_by": "StagedOrchestrator",
                        },
                    },
                )
                _emit(
                    {
                        "event": "stage_failed",
                        "stage": stage_slug,
                        "agent": agent_label,
                        "status": "stage_failed",
                    }
                )
                return _ensure_segment_snapshot_defaults(state)

        state = merge_plan_state(
            state,
            {
                "control": {
                    "status": "completed",
                    "failed_stage": None,
                    "last_updated_by": "StagedOrchestrator",
                },
            },
        )
        _emit({"event": "completed", "status": "completed"})
        return _ensure_segment_snapshot_defaults(state)

    def _run_creator(
        self,
        stage: StageSlug,
        state: PlanState,
        *,
        mode: str,
        audit_sink: PlanAuditSink | None,
        round_iteration: int,
        revision: int,
    ) -> PlanState:
        common = dict(
            mode=mode,
            audit_sink=audit_sink,
            round_iteration=round_iteration,
            orchestration_mode="staged",
            audit_stage=stage,
            audit_revision=revision,
        )
        if stage == "planner":
            return self._planner.run(state, **common)
        if stage == "music_curator":
            return self._curator.run(state, **common)
        if stage == "script_writer":
            return self._writer.run(state, **common)
        raise AIServiceError(f"未知 staged stage：{stage!r}")

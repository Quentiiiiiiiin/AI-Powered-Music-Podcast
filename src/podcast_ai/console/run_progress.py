"""v5.2：阶段一运行态进度 — 日志解析 + 事件归约（纯函数，无 UI）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_RE_ITERATION = re.compile(
    r"PlanOrchestrator\s+iteration=(?P<iter>\d+)\s*,\s*start_agent=(?P<agent>.+?)(?:\s|$)",
)
_RE_RUNNING_AGENT = re.compile(
    r"PlanOrchestrator\s+round:\s*running\s+agent=(?P<agent>.+?)(?:\s|$)",
)
_RE_AISERVICE = re.compile(
    r"PlanOrchestrator\s+在\s+iteration=(?P<iter>\d+)\s+执行\s+(?P<agent>.+?)\s+轮次时发生\s+AIServiceError[：:]\s*(?P<err>.+)",
)
_RE_STAGED_START = re.compile(r"StagedOrchestrator\s+stage=(?P<stage>\S+)\s+start")
_RE_STAGED_REV = re.compile(
    r"StagedOrchestrator\s+stage=(?P<stage>\S+)\s+rev=(?P<rev>\d+)",
)
_RE_STAGED_FAIL = re.compile(r"StagedOrchestrator\s+stage=(?P<stage>\S+)\s+FAILED")


@dataclass
class PlanRunProgress:
    """阶段一运行态洞察的结构化快照。"""

    iteration: int | None = None
    current_agent: str | None = None
    failure_reason: str | None = None
    # v6.0 staged：当前闸门阶段 / 修订轮次（legacy 通常为 None）
    stage: str | None = None
    revision: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "current_agent": self.current_agent,
            "failure_reason": self.failure_reason,
            "stage": self.stage,
            "revision": self.revision,
            "events": list(self.events),
        }


def parse_plan_progress_from_logs(logs: str) -> PlanRunProgress:
    """
    从缓冲日志文本抽取最新 iteration / current_agent / failure_reason。

    兼容现有 orchestrator 日志行；无匹配时字段为 None（single_agent 场景由调用方标注）。
    """
    iteration: int | None = None
    current_agent: str | None = None
    failure_reason: str | None = None
    stage: str | None = None
    revision: int | None = None
    events: list[dict[str, Any]] = []

    for raw_line in (logs or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m_err = _RE_AISERVICE.search(line)
        if m_err:
            iteration = int(m_err.group("iter"))
            current_agent = m_err.group("agent").strip()
            failure_reason = m_err.group("err").strip()
            events.append(
                {
                    "event": "error",
                    "iteration": iteration,
                    "agent": current_agent,
                    "error": failure_reason,
                }
            )
            continue

        m_iter = _RE_ITERATION.search(line)
        if m_iter:
            iteration = int(m_iter.group("iter"))
            current_agent = m_iter.group("agent").strip()
            events.append(
                {
                    "event": "iteration_start",
                    "iteration": iteration,
                    "agent": current_agent,
                }
            )
            continue

        m_agent = _RE_RUNNING_AGENT.search(line)
        if m_agent:
            current_agent = m_agent.group("agent").strip()
            events.append(
                {
                    "event": "agent_start",
                    "iteration": iteration,
                    "agent": current_agent,
                }
            )
            continue

        m_staged_fail = _RE_STAGED_FAIL.search(line)
        if m_staged_fail:
            stage = m_staged_fail.group("stage").strip()
            failure_reason = failure_reason or f"stage_failed:{stage}"
            events.append({"event": "stage_failed", "stage": stage, "status": "stage_failed"})
            continue

        m_staged_rev = _RE_STAGED_REV.search(line)
        if m_staged_rev:
            stage = m_staged_rev.group("stage").strip()
            revision = int(m_staged_rev.group("rev"))
            events.append({"event": "staged_rev", "stage": stage, "revision": revision})
            continue

        m_staged_start = _RE_STAGED_START.search(line)
        if m_staged_start:
            stage = m_staged_start.group("stage").strip()
            events.append({"event": "stage_start", "stage": stage})

    return PlanRunProgress(
        iteration=iteration,
        current_agent=current_agent,
        failure_reason=failure_reason,
        stage=stage,
        revision=revision,
        events=events,
    )


def progress_from_events(
    events: list[dict[str, Any]],
    *,
    failure_reason: str | None = None,
) -> PlanRunProgress:
    """将 orchestrator on_progress 事件列表归约为最新快照。"""
    iteration: int | None = None
    current_agent: str | None = None
    stage: str | None = None
    revision: int | None = None
    err = failure_reason
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if "iteration" in ev and ev["iteration"] is not None:
            try:
                iteration = int(ev["iteration"])
            except (TypeError, ValueError):
                pass
        if "revision" in ev and ev["revision"] is not None:
            try:
                revision = int(ev["revision"])
            except (TypeError, ValueError):
                pass
        st = ev.get("stage")
        if isinstance(st, str) and st.strip():
            stage = st.strip()
        agent = ev.get("agent")
        if isinstance(agent, str) and agent.strip():
            current_agent = agent.strip()
        if ev.get("event") in {"error", "stage_failed"}:
            msg = ev.get("error")
            if isinstance(msg, str) and msg.strip():
                err = msg.strip()
            elif ev.get("event") == "stage_failed" and not err:
                err = f"stage_failed:{stage or 'unknown'}"
    return PlanRunProgress(
        iteration=iteration,
        current_agent=current_agent,
        failure_reason=err,
        stage=stage,
        revision=revision,
        events=list(events),
    )


def merge_progress(
    primary: PlanRunProgress,
    fallback: PlanRunProgress,
) -> PlanRunProgress:
    """优先用 primary（钩子事件）；缺字段时用日志解析补齐。"""
    return PlanRunProgress(
        iteration=primary.iteration if primary.iteration is not None else fallback.iteration,
        current_agent=primary.current_agent or fallback.current_agent,
        failure_reason=primary.failure_reason or fallback.failure_reason,
        stage=primary.stage or fallback.stage,
        revision=primary.revision if primary.revision is not None else fallback.revision,
        events=primary.events or fallback.events,
    )

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


@dataclass
class PlanRunProgress:
    """阶段一运行态洞察的结构化快照。"""

    iteration: int | None = None
    current_agent: str | None = None
    failure_reason: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "current_agent": self.current_agent,
            "failure_reason": self.failure_reason,
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

    return PlanRunProgress(
        iteration=iteration,
        current_agent=current_agent,
        failure_reason=failure_reason,
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
    err = failure_reason
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if "iteration" in ev and ev["iteration"] is not None:
            try:
                iteration = int(ev["iteration"])
            except (TypeError, ValueError):
                pass
        agent = ev.get("agent")
        if isinstance(agent, str) and agent.strip():
            current_agent = agent.strip()
        if ev.get("event") == "error":
            msg = ev.get("error")
            if isinstance(msg, str) and msg.strip():
                err = msg.strip()
    return PlanRunProgress(
        iteration=iteration,
        current_agent=current_agent,
        failure_reason=err,
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
        events=primary.events or fallback.events,
    )

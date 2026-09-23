"""v5.2：阶段一日志进度解析。"""
from __future__ import annotations

from podcast_ai.console.run_progress import (
    merge_progress,
    parse_plan_progress_from_logs,
    progress_from_events,
)


def test_parse_iteration_and_agent_from_logs() -> None:
    logs = """
12:00:01 | INFO | podcast_ai.modules.theme.orchestrator | PlanOrchestrator iteration=1, start_agent=Planner
12:00:02 | INFO | podcast_ai.modules.theme.orchestrator | PlanOrchestrator round: running agent=Planner
12:00:10 | INFO | podcast_ai.modules.theme.orchestrator | PlanOrchestrator round: running agent=Music Curator
12:00:20 | INFO | podcast_ai.modules.theme.orchestrator | PlanOrchestrator iteration=2, start_agent=Script Writer
12:00:21 | INFO | podcast_ai.modules.theme.orchestrator | PlanOrchestrator round: running agent=Critic
""".strip()
    prog = parse_plan_progress_from_logs(logs)
    assert prog.iteration == 2
    assert prog.current_agent == "Critic"
    assert prog.failure_reason is None
    assert any(e.get("event") == "iteration_start" for e in prog.events)


def test_parse_aiservice_error_from_logs() -> None:
    logs = (
        "PlanOrchestrator iteration=1, start_agent=Planner\n"
        "PlanOrchestrator 在 iteration=1 执行 Planner 轮次时发生 AIServiceError：quota exceeded\n"
    )
    prog = parse_plan_progress_from_logs(logs)
    assert prog.iteration == 1
    assert prog.current_agent == "Planner"
    assert prog.failure_reason == "quota exceeded"


def test_progress_from_events_latest_wins() -> None:
    events = [
        {"event": "iteration_start", "iteration": 1, "agent": "Planner"},
        {"event": "agent_start", "iteration": 1, "agent": "Music Curator"},
        {"event": "error", "iteration": 1, "agent": "Music Curator", "error": "boom"},
    ]
    prog = progress_from_events(events)
    assert prog.iteration == 1
    assert prog.current_agent == "Music Curator"
    assert prog.failure_reason == "boom"


def test_progress_from_staged_events_exposes_stage_revision() -> None:
    events = [
        {"event": "stage_start", "stage": "planner", "agent": "Planner", "status": "running"},
        {
            "event": "agent_start",
            "stage": "planner",
            "revision": 0,
            "agent": "Planner",
            "status": "running",
        },
        {
            "event": "agent_start",
            "stage": "music_curator",
            "revision": 1,
            "agent": "Critic",
            "status": "running",
        },
    ]
    prog = progress_from_events(events)
    assert prog.stage == "music_curator"
    assert prog.revision == 1
    assert prog.current_agent == "Critic"


def test_parse_staged_logs_for_stage_revision() -> None:
    logs = """
ThemePlanner multi_agent orchestration_mode=staged
StagedOrchestrator stage=planner start
StagedOrchestrator stage=planner rev=0 critic.pass=false
StagedOrchestrator stage=music_curator start
StagedOrchestrator stage=music_curator rev=2 critic.pass=false
StagedOrchestrator stage=music_curator FAILED after max revisions
""".strip()
    prog = parse_plan_progress_from_logs(logs)
    assert prog.stage == "music_curator"
    assert prog.revision == 2
    assert prog.failure_reason == "stage_failed:music_curator"


def test_merge_progress_fills_gaps() -> None:
    primary = progress_from_events([{"event": "agent_start", "iteration": 3, "agent": "Critic"}])
    fallback = parse_plan_progress_from_logs(
        "PlanOrchestrator 在 iteration=1 执行 Planner 轮次时发生 AIServiceError：x"
    )
    merged = merge_progress(primary, fallback)
    assert merged.iteration == 3
    assert merged.current_agent == "Critic"
    assert merged.failure_reason == "x"


def test_empty_logs_yield_none_fields() -> None:
    prog = parse_plan_progress_from_logs("")
    assert prog.iteration is None
    assert prog.current_agent is None
    assert prog.failure_reason is None

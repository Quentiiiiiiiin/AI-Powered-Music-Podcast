"""v6.8：revision 护栏放宽 + state_partial 旁路 snapshot。"""
from __future__ import annotations

import json
from pathlib import Path

from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.storage.paths import (
    format_audit_failure_snapshot_filename,
    format_audit_staged_failure_snapshot_filename,
    format_audit_staged_state_partial_filename,
    format_audit_state_partial_filename,
)
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.critic_rules import PLANNER_CRITIC_SCORE_DIMS
from podcast_ai.modules.theme.plan_audit import FilePlanAuditSink
from podcast_ai.modules.theme.state import initialize_plan_state, validate_episode_snapshot_subset


class _StubLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def generate(self, messages, **kwargs):  # noqa: ANN001, ARG002
        return json.dumps(self._payload, ensure_ascii=False)


def _scores(v: int) -> dict[str, int]:
    return {d: v for d in PLANNER_CRITIC_SCORE_DIMS}


def _request(tmp: Path) -> EpisodeRequest:
    return EpisodeRequest(topic="Night", duration_minutes=30, language="zh", output_dir=tmp)


def test_revision_new_issue_fingerprint_does_not_abort(tmp_path: Path) -> None:
    state = initialize_plan_state(_request(tmp_path))
    state["critic"]["issues"] = [
        {
            "type": "theme_definition",
            "severity": "critical",
            "location": "meta",
            "problem": "vague",
            "listener_impact": "x",
            "suggestion": "clarify",
        }
    ]
    payload = {
        "critic": {
            "scores": _scores(50),
            "issues": [
                {
                    "type": "theme_definition",
                    "severity": "critical",
                    "location": "meta",
                    "problem": "completely different wording",
                    "listener_impact": "x",
                    "suggestion": "clarify more",
                },
                {
                    "type": "musical_concept",
                    "severity": "minor",
                    "location": "plan",
                    "problem": "brand new issue",
                    "listener_impact": "y",
                    "suggestion": "tweak",
                },
            ],
            "actions": [],
        }
    }
    out = CriticAgent(llm_client=_StubLLM(payload)).run(
        state,
        mode="revision",
        orchestration_mode="staged",
        stage="planner",
    )
    assert len(out["critic"]["issues"]) == 2
    assert out["critic"]["pass"] is False


def test_write_state_partial_writes_legacy_snapshot_beside(tmp_path: Path) -> None:
    state = initialize_plan_state(_request(tmp_path))
    # 故意缺 playlist/script，验证 ensure defaults 后仍能出 snapshot
    for seg in state["segments"]:
        seg.pop("playlist", None)
        seg.pop("script", None)

    sink = FilePlanAuditSink(run_dir=tmp_path / "audit")
    sink.write_state_partial(
        round_iteration=2,
        state_before_round=state,
        error_message="boom",
    )
    partial = tmp_path / "audit" / format_audit_state_partial_filename(2)
    snap = tmp_path / "audit" / format_audit_failure_snapshot_filename(2)
    assert partial.is_file()
    assert snap.is_file()
    raw = json.loads(snap.read_text(encoding="utf-8"))
    validate_episode_snapshot_subset(raw)
    assert "playlists" in raw["segments"][0]
    assert "script" in raw["segments"][0]


def test_write_state_partial_writes_staged_snapshot_beside(tmp_path: Path) -> None:
    state = initialize_plan_state(_request(tmp_path))
    sink = FilePlanAuditSink(run_dir=tmp_path / "audit")
    sink.write_state_partial(
        round_iteration=1,
        state_before_round=state,
        error_message="critic failed",
        stage="music_curator",
        revision=1,
    )
    partial = tmp_path / "audit" / format_audit_staged_state_partial_filename("music_curator", 1)
    snap = tmp_path / "audit" / format_audit_staged_failure_snapshot_filename("music_curator", 1)
    assert partial.is_file()
    assert snap.is_file()
    validate_episode_snapshot_subset(json.loads(snap.read_text(encoding="utf-8")))

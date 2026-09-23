"""v6.5：staged Planner Critic prompt 对齐 Guide（不调 LLM）。"""
from __future__ import annotations

from podcast_ai.modules.theme.prompts_staged import (
    _GUIDE_PLANNER_CRITIC,
    _GUIDES_DIR,
    _load_guide,
    build_critic_staged_messages,
)


def _minimal_state(*, with_prev_critic: bool = False) -> dict:
    state: dict = {
        "meta": {"theme": "Night Drive", "language": "en-US"},
        "global_constraints": {},
        "plan": {},
        "segments": [],
        "critic": {
            "scores": {
                "theme_definition": 60,
                "theme_relationship": 60,
                "musical_concept": 60,
                "segment_differentiation": 60,
                "sequence_narrative": 60,
                "curator_actionability": 60,
                "creative_freedom": 60,
            },
            "issues": [
                {
                    "type": "theme_definition",
                    "severity": "critical",
                    "location": "meta.theme_type",
                    "problem": "theme type vague",
                    "listener_impact": "unfocused",
                    "suggestion": "clarify",
                }
            ],
            "actions": [],
        },
        "control": {},
    }
    if not with_prev_critic:
        state["critic"]["issues"] = []
    return state


def test_planner_critic_guide_file_exists() -> None:
    path = _GUIDES_DIR / _GUIDE_PLANNER_CRITIC
    assert path.is_file()
    text = _load_guide(_GUIDE_PLANNER_CRITIC)
    assert "PLANNER CRITIC" in text
    assert "0-100" in text or "0–100" in text
    assert len(text) > 5000


def test_planner_critic_generation_includes_guide_and_mode() -> None:
    msgs = build_critic_staged_messages(_minimal_state(), "generation", stage="planner")
    assert len(msgs) == 2
    sys = msgs[0]["content"]
    user = msgs[1]["content"]
    assert "MODE: GENERATION" in sys
    assert "Evaluate Planner deliverables from scratch" in sys
    assert "PROMPT_Guide_Planner_Critic.txt" in sys
    assert "You are the PLANNER CRITIC agent" in sys
    assert "Do not redesign the episode yourself" in sys
    assert "0–100" in sys or "0-100" in sys
    assert "FORBIDDEN" in sys
    assert "critic.pass" in sys
    assert "Previous critic snapshot" not in user
    assert len(sys) > 5000


def test_planner_critic_revision_includes_constraints_and_snapshot() -> None:
    msgs = build_critic_staged_messages(
        _minimal_state(with_prev_critic=True),
        "revision",
        stage="planner",
    )
    sys = msgs[0]["content"]
    user = msgs[1]["content"]
    assert "MODE: REVISION" in sys
    assert "FORBIDDEN: inventing NEW issues" in sys
    assert "MUST be >=" in sys or "equal or higher" in sys
    assert "Previous critic snapshot" in user
    assert "theme type vague" in user
    assert "You are the PLANNER CRITIC agent" in sys


def test_script_writer_critic_still_uses_short_prompt() -> None:
    """v6.6：Writer Critic 本轮仍无专用 Guide。"""
    msgs = build_critic_staged_messages(_minimal_state(), "generation", stage="script_writer")
    sys = msgs[0]["content"]
    assert "PROMPT_Guide_Planner_Critic" not in sys
    assert "PROMPT_Guide_Curator_Critic" not in sys
    assert "PLANNER CRITIC THINKING GUIDE" not in sys
    assert "0–100" in sys
    assert "script" in sys.lower()

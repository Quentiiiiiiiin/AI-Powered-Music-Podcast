"""v6.3：staged Planner/Curator prompt 对齐 Guide（不调 LLM）。"""
from __future__ import annotations

from pathlib import Path

from podcast_ai.modules.theme import prompts as legacy_prompts
from podcast_ai.modules.theme.prompts_staged import (
    _GUIDE_MUSIC_CURATOR,
    _GUIDE_PLANNER,
    _GUIDES_DIR,
    _load_guide,
    build_music_curator_staged_messages,
    build_planner_staged_messages,
)


def _minimal_state() -> dict:
    return {
        "meta": {"theme": "Night Drive", "language": "en-US"},
        "global_constraints": {},
        "plan": {},
        "segments": [],
        "critic": {"issues": [], "actions": []},
        "control": {},
    }


def test_guide_files_exist_and_load() -> None:
    assert (_GUIDES_DIR / _GUIDE_PLANNER).is_file()
    assert (_GUIDES_DIR / _GUIDE_MUSIC_CURATOR).is_file()
    planner = _load_guide(_GUIDE_PLANNER)
    curator = _load_guide(_GUIDE_MUSIC_CURATOR)
    assert "Do not choose songs" in planner
    assert "Do not redesign the episode" in curator
    assert len(planner) > 2000
    assert len(curator) > 1000


def test_planner_staged_includes_guide_and_mode_envelope() -> None:
    state = _minimal_state()
    gen = build_planner_staged_messages(state, "generation")
    rev = build_planner_staged_messages(state, "revision")
    assert len(gen) == 2 and gen[0]["role"] == "system"
    sys_gen = gen[0]["content"]
    sys_rev = rev[0]["content"]
    assert "Do not choose songs" in sys_gen
    assert "You are not the Music Curator" in sys_gen
    assert "MODE: GENERATION" in sys_gen
    assert "MODE: REVISION" in sys_rev
    assert "Current gate: PLANNER only" in sys_gen
    assert "Do not invent playlist" in sys_gen or "playlist" in sys_gen.lower()
    assert "Current PlanState JSON" in gen[1]["content"]
    assert "Night Drive" in gen[1]["content"]
    # 显著长于旧短 prompt（防误用摘要）
    assert len(sys_gen) > 3000


def test_curator_staged_includes_guide_and_mode_envelope() -> None:
    state = _minimal_state()
    gen = build_music_curator_staged_messages(state, "generation")
    rev = build_music_curator_staged_messages(state, "revision")
    sys_gen = gen[0]["content"]
    sys_rev = rev[0]["content"]
    assert "Do not redesign the episode" in sys_gen
    assert "You are not the Planner" in sys_gen
    assert "selection_reason" in sys_gen
    assert "Hard constraints" in sys_gen
    assert "MODE: GENERATION" in sys_gen
    assert "MODE: REVISION" in sys_rev
    assert "Do NOT write bpm" in sys_gen or "bpm" in sys_gen.lower()
    assert len(sys_gen) > 2000
    assert "critic.actions" in sys_rev or "REVISION" in sys_rev


def test_legacy_prompts_module_still_has_short_planner_entry() -> None:
    """确认未改 legacy prompts.py 的入口存在（本轮不同步 Guide）。"""
    assert hasattr(legacy_prompts, "build_planner_agent_messages")
    assert hasattr(legacy_prompts, "build_music_curator_agent_messages")
    # legacy 短 prompt 不应被误换成 Guide 全文路径依赖
    src = Path(legacy_prompts.__file__).read_text(encoding="utf-8")
    assert "PROMPT_Guide_Planner" not in src
    assert "PROMPT_Guide_Music-Curator" not in src

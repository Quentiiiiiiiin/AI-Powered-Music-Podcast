"""v6.0：staged（Stage-Gated）专用 prompt；与 legacy `prompts.py` 分离，互不改写。"""
from __future__ import annotations

import json
from textwrap import dedent
from typing import Any


def _state_json(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2)


def build_planner_staged_messages(state: dict[str, Any], mode: str) -> list[dict[str, str]]:
    """Planner 闸门阶段：只交付结构/segments 设计，不写 playlist/script。"""
    m = (mode or "").strip().lower()
    revision_hint = (
        "REVISION：根据 critic.issues / critic.actions 修复本阶段交付物；禁止改写已锁定的无关字段。"
        if m == "revision"
        else "GENERATION：首次生成本阶段交付物。"
    )
    system = dedent(
        f"""
        You are the Planner Agent in a Stage-Gated multi-agent pipeline (v6.0 staged).
        Current gate: PLANNER only. Downstream Music Curator / Script Writer have NOT run yet.

        {revision_hint}

        Write ONLY:
        - meta.theme_description (optional)
        - global_constraints
        - plan.segments_design / plan.emotion_curve
        - segments[*] skeleton: segment_id, order, name, target_duration_seconds, bpm_range, mood, segment_design

        FORBIDDEN: playlist, script, critic.*, control.next_agent.
        Output a single JSON object matching the Planner response schema.
        """
    ).strip()
    user = dedent(
        f"""
        Current PlanState JSON:
        {_state_json(state)}
        """
    ).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_music_curator_staged_messages(state: dict[str, Any], mode: str) -> list[dict[str, str]]:
    """Music Curator 闸门：只写 playlist；结构已由上游锁定。"""
    m = (mode or "").strip().lower()
    revision_hint = (
        "REVISION：根据 critic.issues / actions 修复 playlist；不要改 segments 骨架/script。"
        if m == "revision"
        else "GENERATION：为每个 segment 填写 playlist。"
    )
    system = dedent(
        f"""
        You are the Music Curator Agent in Stage-Gated mode (v6.0 staged).
        Current gate: MUSIC CURATOR. Planner output is LOCKED — do not change structure fields.

        {revision_hint}

        Write ONLY segments[*].playlist items (track, artist, bpm nullable).
        FORBIDDEN: rewriting segment names/durations/design, script, critic.*, control.next_agent.
        Output a single JSON object matching the Music Curator response schema.
        """
    ).strip()
    user = dedent(
        f"""
        Current PlanState JSON:
        {_state_json(state)}
        """
    ).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_script_writer_staged_messages(state: dict[str, Any], mode: str) -> list[dict[str, str]]:
    """Script Writer 闸门：只写 script；结构与 playlist 已锁定。"""
    m = (mode or "").strip().lower()
    revision_hint = (
        "REVISION：根据 critic.issues / actions 修复 script；不要改 playlist 或段落骨架。"
        if m == "revision"
        else "GENERATION：为每个 segment 写 segment_intro 与 between_tracks。"
    )
    system = dedent(
        f"""
        You are the Script Writer Agent in Stage-Gated mode (v6.0 staged).
        Current gate: SCRIPT WRITER. Planner + Music Curator outputs are LOCKED.

        {revision_hint}

        Write ONLY segments[*].script (segment_intro, between_tracks with after_track_index/text).
        FORBIDDEN: playlist, segment structure fields, critic.*, control.next_agent.
        Output a single JSON object matching the Script Writer response schema.
        """
    ).strip()
    user = dedent(
        f"""
        Current PlanState JSON:
        {_state_json(state)}
        """
    ).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_STAGE_FOCUS = {
    "planner": "Evaluate ONLY Planner deliverables (structure / segment design). Ignore missing playlist/script.",
    "music_curator": "Evaluate ONLY playlist quality vs locked structure. Do not demand script changes.",
    "script_writer": "Evaluate ONLY script vs locked structure+playlist.",
}


def build_critic_staged_messages(
    state: dict[str, Any],
    mode: str,
    *,
    stage: str,
) -> list[dict[str, str]]:
    """
    staged Critic：阶段内 pass/scores/issues/actions；**禁止** control.next_agent。
    Orchestrator FSM 负责推进下一阶段。
    """
    focus = _STAGE_FOCUS.get(stage, f"Evaluate current stage={stage!r} deliverables only.")
    m = (mode or "").strip().lower()
    turn = "revision review" if m == "revision" else "first review after generation"
    system = dedent(
        f"""
        You are the Critic Agent in Stage-Gated mode (v6.0 staged).
        Current gate stage: {stage}
        Turn: {turn}

        {focus}

        Output JSON with ONLY:
        - critic.pass (bool)
        - critic.scores {{coherence, emotion_flow, immersion}}
        - critic.issues (array)
        - critic.actions (array; required non-empty when pass=false; target_agent should be the current gate agent)

        FORBIDDEN:
        - control / control.next_agent (Orchestrator decides routing — you MUST NOT include control)
        - rewriting plan content yourself

        If pass=true, issues may be empty and actions may be empty.
        """
    ).strip()
    user = dedent(
        f"""
        Current PlanState JSON:
        {_state_json(state)}
        """
    ).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

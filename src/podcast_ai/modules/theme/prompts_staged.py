"""v6.0：staged（Stage-Gated）专用 prompt；与 legacy `prompts.py` 分离，互不改写。

v6.1：Planner / Curator 字段说明对齐 Schema_Planner_v4 / Schema_Music-Curator_v4。
"""
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
        You are the Planner Agent in a Stage-Gated multi-agent pipeline (v6.1 staged / Schema_Planner_v4).
        Current gate: PLANNER only. Downstream Music Curator / Script Writer have NOT run yet.

        {revision_hint}

        Write ONLY:
        - meta.theme_description / theme_type / theme_subject / theme_relationship
        - global_constraints.energy_strategy / sonic_world[] / avoid[]
        - plan.segment_count / episode_direction / segments_design
        - segments[*]: segment_id, order, name, target_duration_seconds,
          narrative_function, scene, sonic_direction[], lyrical_direction[],
          anchor_tracks[], reference_material[], sequence_direction[], transition_to_next

        FORBIDDEN: playlist, script, critic.*, control.*, bpm_range, mood, segment_design, emotion_curve.
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
    """Music Curator 闸门：只写 playlist（含 v4 解释字段）；结构已由上游锁定。"""
    m = (mode or "").strip().lower()
    revision_hint = (
        "REVISION：根据 critic.issues / actions 修复 playlist；不要改 segments 骨架/script。"
        if m == "revision"
        else "GENERATION：为每个 segment 填写 playlist。"
    )
    system = dedent(
        f"""
        You are the Music Curator Agent in Stage-Gated mode (v6.1 staged / Schema_Music-Curator_v4).
        Current gate: MUSIC CURATOR. Planner output is LOCKED — do not change structure fields.

        {revision_hint}

        Write ONLY segments[*].playlist items with:
          track, artist, selection_reason, sequence_role, planner_alignment[], transition_logic
          (Do NOT write bpm)

        Use Planner fields sonic_direction / sequence_direction / anchor_tracks / lyrical_direction
        to fill planner_alignment.

        FORBIDDEN: rewriting segment names/durations/narrative fields, script, critic.*, control.*.
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
        else "GENERATION：为每个 segment 填写 script。"
    )
    system = dedent(
        f"""
        You are the Script Writer Agent in Stage-Gated mode (v6.0 staged).
        Current gate: SCRIPT WRITER. Planner structure and Curator playlist are LOCKED.

        {revision_hint}

        Write ONLY segments[*].script (segment_intro + between_tracks).
        Prefer narrative_function / scene from Planner when writing host voice.
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


_CRITIC_STAGE_FOCUS = {
    "planner": (
        "Evaluate ONLY Planner deliverables (v4): meta theme_*, global_constraints "
        "(energy_strategy/sonic_world/avoid), plan (segment_count/episode_direction/segments_design), "
        "and segment narrative/sonic fields. Ignore missing playlist/script."
    ),
    "music_curator": (
        "Evaluate ONLY playlist quality vs locked Planner structure, including v4 explanation "
        "fields (selection_reason/sequence_role/planner_alignment/transition_logic). Do not demand script changes."
    ),
    "script_writer": "Evaluate ONLY script vs locked structure+playlist.",
}


def build_critic_staged_messages(
    state: dict[str, Any],
    mode: str,
    *,
    stage: str,
) -> list[dict[str, str]]:
    """staged Critic：阶段内 pass/评分/issues/actions；禁止 control.next_agent。"""
    focus = _CRITIC_STAGE_FOCUS.get(stage, "Evaluate the current stage deliverables only.")
    system = dedent(
        f"""
        You are the Critic Agent in Stage-Gated mode (v6.0 staged).
        Current stage under review: {stage}

        {focus}

        Output ONLY {{"critic": {{pass, scores, issues, actions}}}}.
        FORBIDDEN: control, next_agent, rewriting plan/playlist/script yourself.
        If pass=false, actions must target the current stage's creator agent.
        """
    ).strip()
    user = dedent(
        f"""
        MODE: {mode}
        STAGE: {stage}

        Current PlanState JSON:
        {_state_json(state)}
        """
    ).strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

"""
v6.0+：staged（Stage-Gated）专用 prompt；与 legacy `prompts.py` 分离，互不改写。

v6.3：staged Planner / Music Curator 对齐 Guide 全文
（`guides/PROMPT_Guide_Planner.txt`、`guides/PROMPT_Guide_Music-Curator.txt`）。

v6.5：仅 **Planner 阶段 Critic** 对齐 `guides/PROMPT_Guide_Planner_Critic.txt`
（外壳 + Guide 全文；generation/revision 产品化区分）。
**Curator / Writer Critic 与 legacy Critic 本轮未换 Guide**（仍用通用 staged/legacy 文案，刻度已统一为 0–100）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import Any, Literal

# Guide 旁路文件目录（随包分发；正文勿摘要替换）
_GUIDES_DIR = Path(__file__).resolve().parent / "guides"
_GUIDE_PLANNER = "PROMPT_Guide_Planner.txt"
_GUIDE_MUSIC_CURATOR = "PROMPT_Guide_Music-Curator.txt"
_GUIDE_PLANNER_CRITIC = "PROMPT_Guide_Planner_Critic.txt"


def _state_json(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2)


@lru_cache(maxsize=4)
def _load_guide(filename: str) -> str:
    """读取 `modules/theme/guides/` 下 Guide 全文；缺失则明确报错。"""
    path = _GUIDES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"staged Guide 文件缺失：{path}（请确认已放入 modules/theme/guides/）"
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"staged Guide 文件为空：{path}")
    return text


def _normalize_mode(mode: str) -> Literal["generation", "revision"]:
    m = (mode or "").strip().lower()
    return "revision" if m == "revision" else "generation"


def _mode_envelope(mode: Literal["generation", "revision"], *, revision_focus: str) -> str:
    """GENERATION / REVISION 外壳（极薄；不替代 Guide 正文）。"""
    if mode == "revision":
        return dedent(
            f"""
            MODE: REVISION
            - Apply critic.issues / critic.actions with minimal edits.
            - {revision_focus}
            - Do not rewrite unrelated fields that the critic did not target.
            """
        ).strip()
    return dedent(
        """
        MODE: GENERATION
        - Produce a full deliverable for the current gate from the PlanState and Guide rules.
        """
    ).strip()


def _user_plan_state_message(state: dict[str, Any], *, mode: str, extra: str = "") -> str:
    parts = [f"MODE: {mode}", ""]
    if extra.strip():
        parts.append(extra.strip())
        parts.append("")
    parts.append("Current PlanState JSON:")
    parts.append(_state_json(state))
    return "\n".join(parts).strip()


def build_planner_staged_messages(state: dict[str, Any], mode: str) -> list[dict[str, str]]:
    """
    Planner 闸门：编排外壳 + `PROMPT_Guide_Planner.txt` 全文。

    Guide 来源：`modules/theme/guides/PROMPT_Guide_Planner.txt`
    """
    m = _normalize_mode(mode)
    guide = _load_guide(_GUIDE_PLANNER)
    envelope = dedent(
        f"""
        === STAGE-GATED ORCHESTRATION ENVELOPE (v6.3 staged) ===
        Current gate: PLANNER only. Downstream Music Curator / Script Writer have NOT run yet.

        {_mode_envelope(m, revision_focus="Fix Planner deliverables only; do not invent playlist/script.")}

        === PLANNER THINKING GUIDE (verbatim from {_GUIDE_PLANNER}) ===
        """
    ).strip()
    system = f"{envelope}\n\n{guide}"
    user = _user_plan_state_message(
        state,
        mode=m,
        extra=(
            "REVISION focus: follow critic.issues / actions targeting Planner; "
            "copy untouched Planner fields verbatim when possible."
            if m == "revision"
            else ""
        ),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_music_curator_staged_messages(state: dict[str, Any], mode: str) -> list[dict[str, str]]:
    """
    Music Curator 闸门：编排外壳 + `PROMPT_Guide_Music-Curator.txt` 全文。

    Guide 来源：`modules/theme/guides/PROMPT_Guide_Music-Curator.txt`
    """
    m = _normalize_mode(mode)
    guide = _load_guide(_GUIDE_MUSIC_CURATOR)
    envelope = dedent(
        f"""
        === STAGE-GATED ORCHESTRATION ENVELOPE (v6.3 staged) ===
        Current gate: MUSIC CURATOR. Planner output is LOCKED — do not change structure fields.

        {_mode_envelope(m, revision_focus="Change only playlist items referenced by critic.actions.")}

        === MUSIC CURATOR THINKING GUIDE (verbatim from {_GUIDE_MUSIC_CURATOR}) ===
        """
    ).strip()
    system = f"{envelope}\n\n{guide}"
    user = _user_plan_state_message(
        state,
        mode=m,
        extra=(
            "REVISION focus: keep untouched segment playlists identical to state; "
            "Planner fields are read-only."
            if m == "revision"
            else "Use Planner sonic_direction / sequence_direction / anchor_tracks when filling planner_alignment."
        ),
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_script_writer_staged_messages(state: dict[str, Any], mode: str) -> list[dict[str, str]]:
    """Script Writer 闸门：只写 script；结构与 playlist 已锁定。（v6.3 未纳入 Guide）"""
    m = _normalize_mode(mode)
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
    user = _user_plan_state_message(state, mode=m)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_CRITIC_STAGE_FOCUS = {
    "music_curator": (
        "Evaluate ONLY playlist quality vs locked Planner structure, including v4 explanation "
        "fields (selection_reason/sequence_role/planner_alignment/transition_logic). Do not demand script changes."
    ),
    "script_writer": "Evaluate ONLY script vs locked structure+playlist.",
}


def _planner_critic_mode_envelope(mode: Literal["generation", "revision"]) -> str:
    """v6.5 Planner Critic：generation / revision 产品化外壳（不替代 Guide）。"""
    if mode == "revision":
        return dedent(
            """
            MODE: REVISION
            - Re-score all seven dimensions (0–100 integers).
            - Check previous issues: drop resolved ones; keep unresolved.
            - FORBIDDEN: inventing NEW issues (new location+problem pairs).
            - Regenerate actions only for remaining issues.
            - If the issue count decreased vs previous critic, each dimension score
              MUST be >= the previous score for that dimension (equal or higher only).
            """
        ).strip()
    return dedent(
        """
        MODE: GENERATION
        - Evaluate Planner deliverables from scratch.
        - Score all seven dimensions (0–100), list issues, and list actions.
        """
    ).strip()


def _build_planner_critic_staged_messages(
    state: dict[str, Any],
    mode: Literal["generation", "revision"],
) -> list[dict[str, str]]:
    """staged + stage=planner：Guide 全文 + 薄外壳。"""
    guide = _load_guide(_GUIDE_PLANNER_CRITIC)
    envelope = dedent(
        f"""
        === STAGE-GATED PLANNER CRITIC ENVELOPE (v6.5 staged) ===
        Current stage under review: planner only.
        Ignore missing playlist/script (downstream not run yet).

        {_planner_critic_mode_envelope(mode)}

        OUTPUT CONTRACT:
        - Each dimension score is an integer 0–100 (same scale as the Guide).
        - overall_score is an integer 0–100.
        - Output ONLY {{"critic": {{overall_score, scores, issues, actions}}}}.
        FORBIDDEN: critic.pass, critic.threshold, control, next_agent,
        rewriting plan/playlist/script yourself.
        System derives critic.pass from scores + issues/actions (threshold default 80).

        === PLANNER CRITIC THINKING GUIDE (verbatim from {_GUIDE_PLANNER_CRITIC}) ===
        """
    ).strip()
    system = f"{envelope}\n\n{guide}"

    extra_parts: list[str] = ["STAGE: planner"]
    if mode == "revision":
        critic = state.get("critic") if isinstance(state.get("critic"), dict) else {}
        snapshot = {
            "scores": critic.get("scores"),
            "issues": critic.get("issues"),
            "actions": critic.get("actions"),
            "overall_score": critic.get("overall_score"),
        }
        extra_parts.append(
            "Previous critic snapshot (baseline for REVISION — do not invent new issues):\n"
            + json.dumps(snapshot, ensure_ascii=False, indent=2)
        )
    user = _user_plan_state_message(state, mode=mode, extra="\n\n".join(extra_parts))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_critic_staged_messages(
    state: dict[str, Any],
    mode: str,
    *,
    stage: str,
) -> list[dict[str, str]]:
    """
    staged Critic：阶段内评分/issues/actions；禁止 pass/threshold/control。

    - stage=planner：v6.5 专用 Guide（`PROMPT_Guide_Planner_Critic.txt`）
    - 其他 stage：通用短 prompt（本轮未换 Guide）
    """
    m = _normalize_mode(mode)
    stage_key = (stage or "").strip().lower()
    if stage_key == "planner":
        return _build_planner_critic_staged_messages(state, m)

    focus = _CRITIC_STAGE_FOCUS.get(stage_key, "Evaluate the current stage deliverables only.")
    system = dedent(
        f"""
        You are the Critic Agent in Stage-Gated mode (v6.5 staged).
        Current stage under review: {stage}

        {focus}

        Score these dimensions 0–100 integers:
        theme_definition, theme_relationship, musical_concept,
        segment_differentiation, sequence_narrative,
        curator_actionability, creative_freedom.
        Also set overall_score (0–100).

        Each issue MUST include: type, severity (minor|critical), location,
        problem, listener_impact, suggestion.
        Each action MUST include: target_agent, location, instruction.
        Prefer targeting the current stage's creator agent when actions are needed.

        Output ONLY {{"critic": {{overall_score, scores, issues, actions}}}}.
        FORBIDDEN: critic.pass, critic.threshold, control, next_agent,
        rewriting plan/playlist/script yourself.
        System derives critic.pass from scores + issues/actions (threshold default 80).
        """
    ).strip()
    user = _user_plan_state_message(state, mode=m, extra=f"STAGE: {stage}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

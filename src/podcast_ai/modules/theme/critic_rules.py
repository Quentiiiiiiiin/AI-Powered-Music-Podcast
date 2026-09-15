"""v6.4：Critic 系统规则——派生 critic.pass / threshold，以及 legacy next_agent。

模型不再输出 pass / threshold / control；由本模块根据评分与 issues/actions 计算。
"""
from __future__ import annotations

from typing import Any, Mapping

from podcast_ai.core.exceptions import AIServiceError

# Schema_Critic_v4 分数维（0–10；须严格大于 threshold）
CRITIC_SCORE_DIMS: tuple[str, ...] = (
    "theme_definition",
    "theme_relationship",
    "musical_concept",
    "segment_differentiation",
    "sequence_narrative",
    "curator_actionability",
    "creative_freedom",
)

# 系统阈值（不入模型输出）；默认 6 → 需 ≥7 才算过线
DEFAULT_CRITIC_THRESHOLDS: dict[str, int] = {dim: 6 for dim in CRITIC_SCORE_DIMS}

CRITIC_SEVERITIES: frozenset[str] = frozenset({"minor", "critical"})

# legacy 回修优先序（最先顺位）
_NEXT_AGENT_PRIORITY: tuple[str, ...] = ("Planner", "Music Curator", "Script Writer")

_TARGET_ALIASES: dict[str, str] = {
    "planner": "Planner",
    "music curator": "Music Curator",
    "music_curator": "Music Curator",
    "curator": "Music Curator",
    "script writer": "Script Writer",
    "script_writer": "Script Writer",
    "writer": "Script Writer",
}


def default_critic_thresholds() -> dict[str, int]:
    return dict(DEFAULT_CRITIC_THRESHOLDS)


def normalize_target_agent(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in _TARGET_ALIASES:
        return _TARGET_ALIASES[key]
    # 已是规范名
    for name in _NEXT_AGENT_PRIORITY:
        if raw.strip() == name:
            return name
    raise AIServiceError(
        f"Critic actions.target_agent 非法：{raw!r}（仅支持 Planner / Music Curator / Script Writer）"
    )


def derive_critic_pass(
    critic_body: Mapping[str, Any],
    *,
    thresholds: Mapping[str, int] | None = None,
) -> tuple[bool, dict[str, int]]:
    """
    pass = (∀ dim: score[dim] > threshold[dim]) AND (issues 全为 minor OR actions 为空)。

    返回 (pass, threshold_dict)。缺维 / 非 int / 未知 severity → 报错。
    """
    thr = dict(thresholds) if thresholds is not None else default_critic_thresholds()
    for dim in CRITIC_SCORE_DIMS:
        if dim not in thr or not isinstance(thr[dim], int):
            raise AIServiceError(f"系统 critic.threshold.{dim} 必须是 int。")

    scores = critic_body.get("scores")
    if not isinstance(scores, dict):
        raise AIServiceError("derive_critic_pass：scores 必须是对象。")
    for dim in CRITIC_SCORE_DIMS:
        if dim not in scores:
            raise AIServiceError(f"derive_critic_pass：缺少 scores.{dim}。")
        if not isinstance(scores[dim], int):
            raise AIServiceError(f"derive_critic_pass：scores.{dim} 必须是 int。")

    scores_ok = all(int(scores[dim]) > int(thr[dim]) for dim in CRITIC_SCORE_DIMS)

    issues = critic_body.get("issues")
    if not isinstance(issues, list):
        raise AIServiceError("derive_critic_pass：issues 必须是数组。")
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise AIServiceError(f"derive_critic_pass：issues[{idx}] 必须是对象。")
        sev = issue.get("severity")
        if not isinstance(sev, str) or sev not in CRITIC_SEVERITIES:
            raise AIServiceError(
                f"derive_critic_pass：issues[{idx}].severity 必须是 "
                f"{'/'.join(sorted(CRITIC_SEVERITIES))}（收到：{sev!r}）。"
            )

    actions = critic_body.get("actions")
    if not isinstance(actions, list):
        raise AIServiceError("derive_critic_pass：actions 必须是数组。")

    # 仅有 minor issues，或 actions 为空（含无 issues）
    if len(actions) == 0 or len(issues) == 0:
        issues_ok = True
    else:
        issues_ok = all(
            isinstance(i, dict) and i.get("severity") == "minor" for i in issues
        )

    return (bool(scores_ok and issues_ok), thr)


def derive_next_agent(
    critic_body: Mapping[str, Any],
    *,
    passed: bool,
    previous_next_agent: str | None = None,
) -> str | None:
    """
    legacy：按 actions[*].target_agent 在 Planner → Music Curator → Script Writer 中取最先顺位。

    - pass=true：返回 None（调用方不强制改写路由；编排以 pass 结束）。
    - pass=false 且有 actions：返回优先序最先者。
    - pass=false 且无 actions：回退 previous_next_agent（合法时）否则 Planner，避免空转。
    """
    if passed:
        return None

    actions = critic_body.get("actions") or []
    if not isinstance(actions, list):
        raise AIServiceError("derive_next_agent：actions 必须是数组。")

    seen: set[str] = set()
    for idx, action in enumerate(actions):
        if not isinstance(action, dict):
            raise AIServiceError(f"derive_next_agent：actions[{idx}] 必须是对象。")
        raw = action.get("target_agent")
        if not isinstance(raw, str):
            raise AIServiceError(f"derive_next_agent：actions[{idx}].target_agent 必须是字符串。")
        seen.add(normalize_target_agent(raw))

    if seen:
        for name in _NEXT_AGENT_PRIORITY:
            if name in seen:
                return name
        raise AIServiceError("derive_next_agent：未能从 actions 解析 next_agent。")

    # 无 actions：最小回退
    if previous_next_agent:
        try:
            return normalize_target_agent(previous_next_agent)
        except AIServiceError:
            pass
    return "Planner"

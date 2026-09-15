"""v6.4+：Critic 系统规则——派生 critic.pass / threshold，以及 legacy next_agent。

模型不再输出 pass / threshold / control；由本模块根据评分与 issues/actions 计算。

v6.6：Planner Critic 与 Curator Critic 分数维 / severity 集合分离；
`derive_critic_pass` 接受 `score_dims` / `allowed_severities`。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from podcast_ai.core.exceptions import AIServiceError

# Schema_Critic_v4 / Planner Critic（0–100；须严格大于 threshold）
PLANNER_CRITIC_SCORE_DIMS: tuple[str, ...] = (
    "theme_definition",
    "theme_relationship",
    "musical_concept",
    "segment_differentiation",
    "sequence_narrative",
    "curator_actionability",
    "creative_freedom",
)
# 兼容旧名
CRITIC_SCORE_DIMS = PLANNER_CRITIC_SCORE_DIMS

# Schema_Curator_Critic_v4（与 Planner 维完全分离）
CURATOR_CRITIC_SCORE_DIMS: tuple[str, ...] = (
    "planner_alignment",
    "thematic_relevance",
    "sequence_coherence",
    "audience_listening_quality",
    "track_fitness",
)

# 系统阈值默认 80 → 需 ≥81 才算过线
DEFAULT_CRITIC_THRESHOLD_VALUE = 80

PLANNER_CRITIC_SEVERITIES: frozenset[str] = frozenset({"minor", "critical"})
# Guide：minor | major | critical；仅 minor 计入「仅有 minor」
CURATOR_CRITIC_SEVERITIES: frozenset[str] = frozenset({"minor", "major", "critical"})
CRITIC_SEVERITIES = PLANNER_CRITIC_SEVERITIES  # 兼容旧名

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


def default_critic_thresholds(score_dims: Sequence[str] | None = None) -> dict[str, int]:
    dims = tuple(score_dims) if score_dims is not None else PLANNER_CRITIC_SCORE_DIMS
    return {dim: DEFAULT_CRITIC_THRESHOLD_VALUE for dim in dims}


# 兼容：旧常量（Planner 维）
DEFAULT_CRITIC_THRESHOLDS: dict[str, int] = default_critic_thresholds(PLANNER_CRITIC_SCORE_DIMS)


def normalize_target_agent(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in _TARGET_ALIASES:
        return _TARGET_ALIASES[key]
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
    score_dims: Sequence[str] | None = None,
    allowed_severities: frozenset[str] | None = None,
) -> tuple[bool, dict[str, int]]:
    """
    pass = (∀ dim: score[dim] > threshold[dim]) AND (issues 全为 minor OR actions 为空)。

    `score_dims` 默认 Planner 维；Curator 阶段传入 CURATOR_CRITIC_SCORE_DIMS。
    """
    dims = tuple(score_dims) if score_dims is not None else PLANNER_CRITIC_SCORE_DIMS
    sevs = allowed_severities if allowed_severities is not None else PLANNER_CRITIC_SEVERITIES
    thr = dict(thresholds) if thresholds is not None else default_critic_thresholds(dims)
    for dim in dims:
        if dim not in thr or not isinstance(thr[dim], int):
            raise AIServiceError(f"系统 critic.threshold.{dim} 必须是 int。")

    scores = critic_body.get("scores")
    if not isinstance(scores, dict):
        raise AIServiceError("derive_critic_pass：scores 必须是对象。")
    for dim in dims:
        if dim not in scores:
            raise AIServiceError(f"derive_critic_pass：缺少 scores.{dim}。")
        if not isinstance(scores[dim], int):
            raise AIServiceError(f"derive_critic_pass：scores.{dim} 必须是 int。")

    scores_ok = all(int(scores[dim]) > int(thr[dim]) for dim in dims)

    issues = critic_body.get("issues")
    if not isinstance(issues, list):
        raise AIServiceError("derive_critic_pass：issues 必须是数组。")
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise AIServiceError(f"derive_critic_pass：issues[{idx}] 必须是对象。")
        sev = issue.get("severity")
        if not isinstance(sev, str) or sev not in sevs:
            raise AIServiceError(
                f"derive_critic_pass：issues[{idx}].severity 必须是 "
                f"{'/'.join(sorted(sevs))}（收到：{sev!r}）。"
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

    if previous_next_agent:
        try:
            return normalize_target_agent(previous_next_agent)
        except AIServiceError:
            pass
    return "Planner"


def _issue_fingerprint(issue: Mapping[str, Any]) -> str:
    """revision 对照用：location + problem（规范化空白）。不做语义等价判断。"""
    loc = str(issue.get("location") or "").strip()
    problem = str(issue.get("problem") or "").strip()
    return f"{loc}::{problem}"


def assert_staged_revision_constraints(
    previous_critic: Mapping[str, Any] | None,
    new_body: Mapping[str, Any],
    *,
    score_dims: Sequence[str],
    label: str = "staged revision",
) -> None:
    """
    staged Critic revision 轻量护栏：
    - 禁止新增 location+problem 指纹；
    - 若 issues 条数下降，则各维 score 不得低于上一轮（报错，不夹紧）。
    """
    if not previous_critic:
        return

    prev_issues = previous_critic.get("issues") or []
    new_issues = new_body.get("issues") or []
    if not isinstance(prev_issues, list) or not isinstance(new_issues, list):
        raise AIServiceError(f"{label} 校验：issues 必须是数组。")

    prev_keys = {_issue_fingerprint(i) for i in prev_issues if isinstance(i, dict)}
    new_keys = {_issue_fingerprint(i) for i in new_issues if isinstance(i, dict)}
    novel = sorted(k for k in new_keys if k and k not in prev_keys)
    if novel:
        raise AIServiceError(
            f"{label} 禁止新增 issues（相对上一轮 location+problem）：" + "; ".join(novel[:5])
        )

    if len(new_issues) >= len(prev_issues):
        return

    prev_scores = previous_critic.get("scores")
    new_scores = new_body.get("scores")
    if not isinstance(prev_scores, dict) or not isinstance(new_scores, dict):
        raise AIServiceError(f"{label} 校验：scores 必须是对象。")

    for dim in score_dims:
        prev_v = prev_scores.get(dim)
        new_v = new_scores.get(dim)
        if not isinstance(prev_v, int) or not isinstance(new_v, int):
            raise AIServiceError(f"{label} 校验：scores.{dim} 必须是 int。")
        if new_v < prev_v:
            raise AIServiceError(
                f"{label}：issues 已减少时 scores.{dim} 不得低于上一轮 "
                f"（prev={prev_v}, new={new_v}）。"
            )


def assert_planner_revision_constraints(
    previous_critic: Mapping[str, Any] | None,
    new_body: Mapping[str, Any],
) -> None:
    """v6.5 兼容入口：Planner 维 revision 护栏。"""
    assert_staged_revision_constraints(
        previous_critic,
        new_body,
        score_dims=PLANNER_CRITIC_SCORE_DIMS,
        label="planner revision",
    )

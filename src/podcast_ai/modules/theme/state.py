from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest

PLAN_STATE_SCHEMA_VERSION = "3.0"
DEFAULT_MAX_ITERATIONS = 3

_TOP_LEVEL_KEYS = ("schema_version", "meta", "global_constraints", "plan", "segments", "critic", "control")
_CONTROL_REQUIRED_KEYS = ("max_iterations", "iteration", "status", "next_agent", "last_updated_by")
_META_REQUIRED_KEYS = ("request_id", "theme", "theme_description", "language", "target_duration_seconds", "overall_bpm_range")
_PLAN_REQUIRED_KEYS = ("segments_design", "emotion_curve")
_CRITIC_REQUIRED_KEYS = ("pass", "scores", "threshold", "issues", "actions")
_CRITIC_SCORE_KEYS = ("coherence", "emotion_flow", "immersion")


PlanState = Dict[str, Any]


def initialize_plan_state(
    request: EpisodeRequest,
    *,
    request_id: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> PlanState:
    """
    创建 v3.0 多 Agent 的共享状态。

    保持默认值最小化：仅放置 orchestrator/agent 都需要的公共字段。
    """
    rid = request_id or str(uuid.uuid4())
    language = "zh-CN" if request.language == "zh" else "en-US"
    target_duration_seconds = request.duration_minutes * 60

    # 为了与 state_schema.json 的 segments[*] 结构对齐，这里放置一个最小 segment 模板。
    # 后续 Planner 会回写 name/target_duration_seconds/bpm_range/mood/segment_design，并可按索引追加更多 segments。
    default_segment = {
        "segment_id": "seg_01",
        "order": 1,
        "name": "",
        "target_duration_seconds": max(1, int(target_duration_seconds // 3)),
        "bpm_range": None,
        "mood": "",
        "segment_design": "",
        "playlist": [],
        "script": {
            "segment_intro": "",
            "between_tracks": [
                {
                    "after_track_index": 0,
                    "text": None,
                }
            ],
        },
    }
    return {
        "schema_version": f"v{PLAN_STATE_SCHEMA_VERSION}",
        "meta": {
            "request_id": rid,
            "theme": request.topic,
            "theme_description": "",
            "language": language,
            "target_duration_seconds": target_duration_seconds,
            "overall_bpm_range": None,
        },
        "global_constraints": {
            "tone": "",
            "language_style": "",
            "avoid": [],
        },
        "plan": {
            "segments_design": "",
            "emotion_curve": [],
        },
        "segments": [default_segment],
        "critic": {
            "pass": False,
            "scores": {"coherence": 0, "emotion_flow": 0, "immersion": 0},
            "threshold": {"coherence": 7, "emotion_flow": 7, "immersion": 7},
            "issues": [],
            "actions": [],
        },
        "control": {
            "max_iterations": max_iterations,
            "iteration": 1,
            "status": "draft",
            "next_agent": "Planner",
            "last_updated_by": "Orchestrator",
        },
    }


def merge_plan_state(base: PlanState, patch: Dict[str, Any]) -> PlanState:
    """
    字段级 merge：
    - dict: 递归合并
    - list：
      - 若 list 内元素为 dict，则按索引进行字段级合并（保留原有字段，追加/覆盖目标字段）
      - 否则直接覆盖
    """
    merged = deepcopy(base)
    _merge_dict_inplace(merged, patch)
    return merged


def _merge_dict_inplace(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_dict_inplace(target[key], value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            _merge_list_inplace(target[key], value)
        else:
            target[key] = deepcopy(value)


def _merge_list_inplace(target_list: List[Any], patch_list: List[Any]) -> None:
    """
    为多 Agent 回写设计的最小 list merge：
    - target_list 与 patch_list 内元素若均为 dict，则按索引递归合并，避免覆盖掉未被写入的字段。
    - 其它情况：patch_list 直接覆盖。
    """
    if not patch_list:
        # patch 为空：保持原 list（避免无意义覆盖）
        return
    if not target_list:
        target_list[:] = deepcopy(patch_list)
        return
    if not all(isinstance(x, dict) for x in target_list) or not all(isinstance(x, dict) for x in patch_list):
        target_list[:] = deepcopy(patch_list)
        return

    # dict list：按索引合并
    for idx, patch_item in enumerate(patch_list):
        if idx < len(target_list) and isinstance(target_list[idx], dict) and isinstance(patch_item, dict):
            _merge_dict_inplace(target_list[idx], patch_item)
        else:
            target_list.append(deepcopy(patch_item))


def get_missing_required_fields(state: PlanState) -> List[str]:
    missing: List[str] = []
    for key in _TOP_LEVEL_KEYS:
        if key not in state:
            missing.append(key)

    control = state.get("control")
    if isinstance(control, dict):
        for key in _CONTROL_REQUIRED_KEYS:
            if key not in control:
                missing.append(f"control.{key}")
    else:
        missing.append("control")

    meta = state.get("meta")
    if isinstance(meta, dict):
        for key in _META_REQUIRED_KEYS:
            if key not in meta:
                missing.append(f"meta.{key}")
    elif "meta" in state:
        missing.append("meta")

    plan = state.get("plan")
    if isinstance(plan, dict):
        for key in _PLAN_REQUIRED_KEYS:
            if key not in plan:
                missing.append(f"plan.{key}")
    elif "plan" in state:
        missing.append("plan")

    critic = state.get("critic")
    if isinstance(critic, dict):
        for key in _CRITIC_REQUIRED_KEYS:
            if key not in critic:
                missing.append(f"critic.{key}")
    elif "critic" in state:
        missing.append("critic")
    return missing


def validate_plan_state_schema(state: PlanState) -> Tuple[bool, List[str]]:
    """
    最小 schema 合法性校验（类型 + 必填字段）。
    返回 (is_valid, errors)。
    """
    errors: List[str] = []
    missing = get_missing_required_fields(state)
    if missing:
        errors.extend(f"missing required field: {name}" for name in missing)

    if "schema_version" in state and not isinstance(state.get("schema_version"), str):
        errors.append("schema_version must be string")
    if "meta" in state and not isinstance(state.get("meta"), dict):
        errors.append("meta must be object")
    if "global_constraints" in state and not isinstance(state.get("global_constraints"), dict):
        errors.append("global_constraints must be object")
    if "plan" in state and not isinstance(state.get("plan"), dict):
        errors.append("plan must be object")
    if "segments" in state and not isinstance(state.get("segments"), list):
        errors.append("segments must be array")
    if "critic" in state and not isinstance(state.get("critic"), dict):
        errors.append("critic must be object")
    if "control" in state and not isinstance(state.get("control"), dict):
        errors.append("control must be object")

    meta = state.get("meta")
    if isinstance(meta, dict):
        if "request_id" in meta and not isinstance(meta.get("request_id"), str):
            errors.append("meta.request_id must be string")
        if "theme" in meta and not isinstance(meta.get("theme"), str):
            errors.append("meta.theme must be string")
        if "theme_description" in meta and not isinstance(meta.get("theme_description"), str):
            errors.append("meta.theme_description must be string")
        if "language" in meta and not isinstance(meta.get("language"), str):
            errors.append("meta.language must be string")
        if "target_duration_seconds" in meta and not isinstance(meta.get("target_duration_seconds"), int):
            errors.append("meta.target_duration_seconds must be int")
        if "overall_bpm_range" in meta:
            bpm_range = meta.get("overall_bpm_range")
            if bpm_range is not None:
                if not isinstance(bpm_range, list) or len(bpm_range) != 2:
                    errors.append("meta.overall_bpm_range must be [min, max] or null")

    plan = state.get("plan")
    if isinstance(plan, dict):
        if "segments_design" in plan and not isinstance(plan.get("segments_design"), str):
            errors.append("plan.segments_design must be string")
        if "emotion_curve" in plan and not isinstance(plan.get("emotion_curve"), list):
            errors.append("plan.emotion_curve must be array")

    critic = state.get("critic")
    if isinstance(critic, dict):
        if "pass" in critic and not isinstance(critic.get("pass"), bool):
            errors.append("critic.pass must be bool")
        if "scores" in critic and not isinstance(critic.get("scores"), dict):
            errors.append("critic.scores must be object")
        if "threshold" in critic and not isinstance(critic.get("threshold"), dict):
            errors.append("critic.threshold must be object")
        if "issues" in critic and not isinstance(critic.get("issues"), list):
            errors.append("critic.issues must be array")
        if "actions" in critic and not isinstance(critic.get("actions"), list):
            errors.append("critic.actions must be array")

        scores = critic.get("scores")
        if isinstance(scores, dict):
            for key in _CRITIC_SCORE_KEYS:
                if key in scores and not isinstance(scores.get(key), int):
                    errors.append(f"critic.scores.{key} must be int")
        threshold = critic.get("threshold")
        if isinstance(threshold, dict):
            for key in _CRITIC_SCORE_KEYS:
                if key in threshold and not isinstance(threshold.get(key), int):
                    errors.append(f"critic.threshold.{key} must be int")

    control = state.get("control")
    if isinstance(control, dict):
        if "max_iterations" in control and not isinstance(control.get("max_iterations"), int):
            errors.append("control.max_iterations must be int")
        elif isinstance(control.get("max_iterations"), int) and control["max_iterations"] <= 0:
            errors.append("control.max_iterations must be > 0")

        if "iteration" in control and not isinstance(control.get("iteration"), int):
            errors.append("control.iteration must be int")
        if "status" in control and not isinstance(control.get("status"), str):
            errors.append("control.status must be string")
        if "next_agent" in control and not isinstance(control.get("next_agent"), str):
            errors.append("control.next_agent must be string")
        if "last_updated_by" in control and not isinstance(control.get("last_updated_by"), str):
            errors.append("control.last_updated_by must be string")

    return (len(errors) == 0, errors)


def assert_plan_state_valid(state: PlanState) -> None:
    ok, errors = validate_plan_state_schema(state)
    if not ok:
        raise AIServiceError(f"PlanState schema 校验失败：{'; '.join(errors)}")

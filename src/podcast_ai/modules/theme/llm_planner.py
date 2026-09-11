from __future__ import annotations

import json
import logging
import uuid
from math import ceil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Literal

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodePlan, EpisodeRequest, EpisodeSegment, PlaylistItem
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.agent_response_schemas import (
    SINGLE_AGENT_STATE_SUBSET_SCHEMA,
    build_openrouter_response_format,
)
from podcast_ai.modules.theme.json_repair import repair_and_standardize_json
from podcast_ai.modules.theme.prompts import build_theme_planner_messages
from podcast_ai.modules.theme.state import PlanState, validate_state_subset_for_single_agent

logger = logging.getLogger(__name__)


def _suggest_segment_count(duration_minutes: int) -> int:
    if duration_minutes <= 30:
        return 3
    if duration_minutes <= 60:
        return 4
    if duration_minutes <= 90:
        return 5
    return 6


def _parse_overall_bpm_range(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    value = data.get("overall_bpm_range")
    if not value:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        lo = int(value[0])
        hi = int(value[1])
        return (lo, hi)
    except Exception:  # noqa: BLE001
        return None


class ThemePlanner:
    """基于 LLM 的节目主题与结构规划器。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._llm = llm_client or get_default_llm_client(self._settings)

    def generate_plan(self, request: EpisodeRequest, *, use_orchestrator: bool = True) -> EpisodePlan:
        """
        生成 EpisodePlan。

        - v2.x：默认路径为单次 LLM 调用（use_orchestrator=False）
        - v3.0：当 use_orchestrator=True 时，使用多 Agent Orchestrator + PlanState 转 EpisodePlan
        """
        if use_orchestrator:
            return self._generate_plan_v3_orchestrator(request)

        target_duration_seconds = request.duration_minutes * 60
        segments_hint = _suggest_segment_count(request.duration_minutes)

        messages = build_theme_planner_messages(request, segments_hint)

        try:
            raw = self._llm.generate(messages, temperature=0.7)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 LLM 生成 EpisodePlan 失败：{exc!r}") from exc

        logger.debug("Raw LLM plan output (truncated): %s", raw[:1000])

        try:
            repaired = repair_and_standardize_json(raw)
            data = json.loads(repaired)
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError("LLM 返回结果不是有效的 JSON。") from exc

        if not isinstance(data, dict):
            raise AIServiceError("LLM 返回的 JSON 顶层不是对象。")

        style_description = str(data.get("style_description") or "")
        overall_bpm_range = _parse_overall_bpm_range(data)

        segments_data = data.get("segments")
        if not isinstance(segments_data, list) or not segments_data:
            raise AIServiceError("LLM 返回的 EpisodePlan 中 segments 为空。")

        segments: List[EpisodeSegment] = []
        for idx, seg in enumerate(segments_data):
            if not isinstance(seg, dict):
                raise AIServiceError(f"segments[{idx}] 不是对象。")

            name = str(seg.get("name") or f"Segment {idx + 1}")
            raw_duration = seg.get("target_duration_seconds")
            try:
                seg_duration = int(raw_duration) if raw_duration is not None else None
            except Exception:  # noqa: BLE001
                seg_duration = None

            if raw_duration is not None and seg_duration is None:
                raise AIServiceError(
                    f"segments[{idx}].target_duration_seconds 必须是整数或可转换为整数的值。",
                )

            bpm_range_val = seg.get("bpm_range")
            bpm_range: Optional[Tuple[int, int]] = None
            if isinstance(bpm_range_val, (list, tuple)) and len(bpm_range_val) == 2:
                try:
                    bpm_range = (int(bpm_range_val[0]), int(bpm_range_val[1]))
                except Exception:  # noqa: BLE001
                    bpm_range = None

            mood = str(seg.get("mood") or "")
            host_script = str(seg.get("host_script") or "")
            if not host_script.strip():
                raise AIServiceError(f"segments[{idx}].host_script 为空或缺失。")

            playlist_items_raw = seg.get("target_playlist") or []
            if not isinstance(playlist_items_raw, list):
                raise AIServiceError(f"segments[{idx}].target_playlist 不是数组。")
            if not playlist_items_raw:
                raise AIServiceError(f"segments[{idx}].target_playlist 为空。")

            playlist_items: List[PlaylistItem] = []
            for item_idx, item in enumerate(playlist_items_raw):
                if not isinstance(item, dict):
                    raise AIServiceError(
                        f"segments[{idx}].target_playlist[{item_idx}] 不是对象。",
                    )

                recommended = item.get("recommended_tracks") or []
                if not isinstance(recommended, list):
                    raise AIServiceError(
                        f"segments[{idx}].target_playlist[{item_idx}].recommended_tracks 不是数组。",
                    )
                recommended = [str(x) for x in recommended]

                search_hints = item.get("search_hints") or {}
                if not isinstance(search_hints, dict):
                    search_hints = {}

                playlist_items.append(
                    PlaylistItem(
                        segment_name=name,
                        recommended_tracks=recommended,
                        search_hints=search_hints,
                    ),
                )

            segments.append(
                EpisodeSegment(
                    name=name,
                    target_duration_seconds=seg_duration or ceil(target_duration_seconds / max(len(segments_data), 1)),
                    bpm_range=bpm_range,
                    mood=mood,
                    host_script=host_script,
                    target_playlist=playlist_items,
                ),
            )

        plan = EpisodePlan(
            segments=segments,
            target_duration_seconds=target_duration_seconds,
            overall_bpm_range=overall_bpm_range,
            style_description=style_description,
            plan_id=str(uuid.uuid4()),
        )
        return plan

    def generate_plan_and_state(
        self,
        request: EpisodeRequest,
        *,
        agent_mode: Literal["single_agent", "multi_agent"] = "multi_agent",
        on_progress: Callable[[dict], None] | None = None,
    ) -> tuple[EpisodePlan, PlanState]:
        """
        v3.1：同时生成 EpisodePlan（阶段二可用）与 PlanState（落盘为 state.json）。

        - multi_agent：按 app.orchestration_mode 走 staged 或 legacy Orchestrator → PlanState → EpisodePlan
        - single_agent：单次 LLM 先生成 EpisodePlan，再映射为 PlanState（critic/control 置 null）
        - on_progress：v5.2 可选进度钩子（仅 multi_agent 转发；默认不传）
        """
        if agent_mode == "multi_agent":
            state = self._generate_plan_state_v3_orchestrator(request, on_progress=on_progress)
            return _episode_plan_from_state(state), state

        if on_progress is not None:
            on_progress({"event": "agent_start", "iteration": None, "agent": "single_agent"})
        state = self._generate_plan_state_single_agent_subset(request)
        return _episode_plan_from_state(state), state

    def generate_plan_state(
        self,
        request: EpisodeRequest,
        *,
        agent_mode: Literal["single_agent", "multi_agent"] = "multi_agent",
        on_progress: Callable[[dict], None] | None = None,
    ) -> PlanState:
        """仅生成 PlanState。通常由 pipeline 保存为 state.json。"""
        if agent_mode == "multi_agent":
            return self._generate_plan_state_v3_orchestrator(request, on_progress=on_progress)
        if on_progress is not None:
            on_progress({"event": "agent_start", "iteration": None, "agent": "single_agent"})
        return self._generate_plan_state_single_agent_subset(request)

    def _generate_plan_state_v3_orchestrator(
        self,
        request: EpisodeRequest,
        *,
        on_progress: Callable[[dict], None] | None = None,
    ) -> PlanState:
        """
        multi_agent：按 app.orchestration_mode 分发。

        - staged（默认）：Stage-Gated FSM（orchestrator_staged）
        - legacy：旧全局 Critic 回修（orchestrator，冻结）

        阶段失败时 Orchestrator 仍返回带标记的 PlanState，供 pipeline 落盘 snapshot。
        """
        mode = getattr(self._settings.app, "orchestration_mode", "staged") or "staged"
        mode = str(mode).strip().lower()
        logger.info("ThemePlanner multi_agent orchestration_mode=%s", mode)

        if mode == "legacy":
            from podcast_ai.modules.theme.orchestrator import PlanOrchestrator

            return PlanOrchestrator(settings=self._settings).run(request, on_progress=on_progress)

        from podcast_ai.modules.theme.orchestrator_staged import StagedPlanOrchestrator

        return StagedPlanOrchestrator(settings=self._settings).run(request, on_progress=on_progress)

    def _generate_plan_v3_orchestrator(self, request: EpisodeRequest) -> EpisodePlan:
        """兼容旧入口：走与 generate_plan_state(multi_agent) 相同的 mode 分发。"""
        state = self._generate_plan_state_v3_orchestrator(request)
        return _episode_plan_from_state(state)

    def _generate_plan_state_single_agent_subset(self, request: EpisodeRequest) -> PlanState:
        """
        v3.7：single_agent 直接产出 PlanState 子集（无 critic/control）。
        """
        segments_hint = _suggest_segment_count(request.duration_minutes)
        messages = build_theme_planner_messages(request, segments_hint)
        request_id = str(uuid.uuid4())

        seed_state: PlanState = {
            "meta": {"request_id": request_id},
            "control": {"iteration": 1},
        }

        gen_kwargs: Dict[str, Any] = {"temperature": 0.7}
        structured = should_use_structured_output(self._settings.llm)
        if structured:
            gen_kwargs["response_format"] = build_openrouter_response_format(
                "podcast_single_agent_state_subset",
                SINGLE_AGENT_STATE_SUBSET_SCHEMA,
            )

        try:
            raw = self._llm.generate(messages, **gen_kwargs)
        except AIServiceError as exc:
            raise AIServiceError(f"single_agent 规划调用失败（request_id={request_id}）：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"single_agent 调用 LLM 失败（request_id={request_id}）：{exc!r}") from exc

        data = parse_agent_json_response(
            agent_label="Theme Planner(single_agent)",
            raw=raw,
            state=seed_state,
            structured=structured,
            allow_repair_fallback=not structured,
            output_dir=Path(self._settings.app.output_dir),
        )
        try:
            validate_state_subset_for_single_agent(data)
        except AIServiceError as exc:
            raise AIServiceError(f"single_agent state 子集校验失败（request_id={request_id}）：{exc}") from exc
        return data


def _episode_plan_from_state(state: PlanState) -> EpisodePlan:
    """根据最终 PlanState 构造 EpisodePlan。"""
    meta = state.get("meta") or {}
    segments_data = state.get("segments") or []
    critic = state.get("critic") or {}

    target_duration_seconds = int(meta.get("target_duration_seconds") or 0) or 1

    overall_bpm_range = None
    meta_bpm = meta.get("overall_bpm_range")
    if isinstance(meta_bpm, list) and len(meta_bpm) == 2:
        try:
            overall_bpm_range = (int(meta_bpm[0]), int(meta_bpm[1]))
        except Exception:  # noqa: BLE001
            overall_bpm_range = None

    segments: List[EpisodeSegment] = []
    for idx, seg in enumerate(segments_data):
        if not isinstance(seg, dict):
            continue

        name = str(seg.get("name") or f"Segment {idx + 1}")
        raw_duration = seg.get("target_duration_seconds")
        try:
            seg_duration = int(raw_duration) if raw_duration is not None else 0
        except Exception:  # noqa: BLE001
            seg_duration = 0
        if seg_duration <= 0:
            seg_duration = max(1, target_duration_seconds // max(len(segments_data), 1))

        bpm_range_val = seg.get("bpm_range")
        bpm_range: Optional[Tuple[int, int]] = None
        if isinstance(bpm_range_val, list) and len(bpm_range_val) == 2:
            try:
                bpm_range = (int(bpm_range_val[0]), int(bpm_range_val[1]))
            except Exception:  # noqa: BLE001
                bpm_range = None

        mood = str(seg.get("mood") or "")

        script = seg.get("script") or {}
        host_script = str(script.get("segment_intro") or "")

        playlist_items: List[PlaylistItem] = []
        playlist = seg.get("playlist") or []
        if isinstance(playlist, list):
            for item in playlist:
                if not isinstance(item, dict):
                    continue
                track_name = str(item.get("track") or "")
                artist = str(item.get("artist") or "") if item.get("artist") is not None else ""
                bpm = item.get("bpm")
                label = track_name
                if artist:
                    label = f"{track_name} - {artist}"
                recommended_tracks = [label] if label else []
                search_hints: Dict[str, Any] = {}
                if bpm is not None:
                    try:
                        search_hints["bpm"] = int(bpm)
                    except Exception:  # noqa: BLE001
                        pass
                playlist_items.append(
                    PlaylistItem(
                        segment_name=name,
                        recommended_tracks=recommended_tracks,
                        search_hints=search_hints,
                    ),
                )

        segments.append(
            EpisodeSegment(
                name=name,
                target_duration_seconds=seg_duration,
                bpm_range=bpm_range,
                mood=mood,
                host_script=host_script,
                target_playlist=playlist_items,
            ),
        )

    critic_summary: Dict[str, Any] = {
        "pass": critic.get("pass"),
        "scores": critic.get("scores"),
        "issues": critic.get("issues"),
        "actions": critic.get("actions"),
    }

    trace_raw = meta.get("generation_trace") or []
    generation_trace: List[Dict[str, Any]] = []
    if isinstance(trace_raw, list):
        for name in trace_raw:
            generation_trace.append({"agent": str(name)})

    return EpisodePlan(
        segments=segments,
        target_duration_seconds=target_duration_seconds,
        overall_bpm_range=overall_bpm_range,
        style_description=str(state.get("plan", {}).get("segments_design") or ""),
        plan_id=str(uuid.uuid4()),
        critic_summary=critic_summary,
        generation_trace=generation_trace or None,
    )



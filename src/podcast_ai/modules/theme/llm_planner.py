from __future__ import annotations

import json
import logging
import uuid
from math import ceil
from typing import Any, Dict, List, Optional, Tuple

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodePlan, EpisodeRequest, EpisodeSegment, PlaylistItem
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.prompts import build_theme_planner_messages

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

    def generate_plan(self, request: EpisodeRequest) -> EpisodePlan:
        """
        调用 LLM 生成 EpisodePlan，并解析为内部数据模型。
        """
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
            data = json.loads(raw)
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


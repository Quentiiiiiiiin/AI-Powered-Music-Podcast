from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.modules.theme.agent_response_schemas import (
    build_openrouter_response_format,
    build_script_writer_response_schema,
)
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_SCRIPT_WRITER, PlanAuditSink
from podcast_ai.modules.theme.prompts import build_script_writer_agent_messages
from podcast_ai.modules.theme.prompts_staged import build_script_writer_staged_messages
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Script Writer Agent"

_SCRIPT_WRITER_SEGMENT_ALLOWED_KEYS = {"segment_id", "script"}
_SCRIPT_ALLOWED_KEYS = {"segment_intro", "between_tracks"}
_BETWEEN_TRACK_ALLOWED_KEYS = {"after_track_index", "text"}

_ZH_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_RE = re.compile(r"[A-Za-z]")


def _text_matches_language(text: str, language: str) -> bool:
    """
    最小语言一致性校验（启发式）：
    - zh：要求至少包含一个汉字
    - en：要求至少包含一个英文字母
    """
    if language.startswith("zh"):
        return bool(_ZH_RE.search(text))
    if language.startswith("en"):
        return bool(_EN_RE.search(text))
    return True


def _sanitize_script_writer_patch(
    data: Dict[str, Any],
    *,
    expected_segments: int,
    language: str,
) -> Dict[str, Any]:
    if set(data.keys()) - {"segments"}:
        forbidden_top = sorted(set(data.keys()) - {"segments"})
        raise AIServiceError(f"Script Writer 越权写入顶层字段：{', '.join(forbidden_top)}。")

    segments = data.get("segments")
    if not isinstance(segments, list):
        raise AIServiceError("Script Writer 输出中 segments 必须是数组。")
    if len(segments) != expected_segments:
        raise AIServiceError(
            f"Script Writer 输出 segments 长度不匹配：expected {expected_segments}, got {len(segments)}。",
        )

    sanitized_segments: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise AIServiceError(f"segments[{idx}] 必须是对象。")
        forbidden_seg = [k for k in seg.keys() if k not in _SCRIPT_WRITER_SEGMENT_ALLOWED_KEYS]
        if forbidden_seg:
            raise AIServiceError(f"Script Writer 越权写入 segments[{idx}] 字段：{', '.join(forbidden_seg)}。")

        script = seg.get("script")
        if not isinstance(script, dict):
            raise AIServiceError(f"segments[{idx}].script 必须是对象。")
        forbidden_script = [k for k in script.keys() if k not in _SCRIPT_ALLOWED_KEYS]
        if forbidden_script:
            raise AIServiceError(
                f"Script Writer 越权写入 segments[{idx}].script 字段：{', '.join(forbidden_script)}。",
            )

        segment_intro = script.get("segment_intro")
        if not isinstance(segment_intro, str):
            raise AIServiceError(f"segments[{idx}].script.segment_intro 必须是字符串。")
        if not segment_intro.strip():
            raise AIServiceError(f"segments[{idx}].script.segment_intro 为空或缺失。")
        if not _text_matches_language(segment_intro, language):
            raise AIServiceError("segments[*].script.segment_intro 语言不一致。")

        between_tracks = script.get("between_tracks")
        if not isinstance(between_tracks, list):
            raise AIServiceError(f"segments[{idx}].script.between_tracks 必须是数组。")

        sanitized_between_tracks: List[Dict[str, Any]] = []
        for bt_idx, bt in enumerate(between_tracks):
            if not isinstance(bt, dict):
                raise AIServiceError(f"segments[{idx}].script.between_tracks[{bt_idx}] 必须是对象。")
            forbidden_bt = [k for k in bt.keys() if k not in _BETWEEN_TRACK_ALLOWED_KEYS]
            if forbidden_bt:
                raise AIServiceError(
                    f"Script Writer 越权写入 segments[{idx}].script.between_tracks[{bt_idx}] 字段：{', '.join(forbidden_bt)}。",
                )

            after_track_index = bt.get("after_track_index")
            if not isinstance(after_track_index, int):
                raise AIServiceError(
                    f"segments[{idx}].script.between_tracks[{bt_idx}].after_track_index 必须是整数。",
                )

            text = bt.get("text")
            if text is not None and not isinstance(text, str):
                raise AIServiceError(
                    f"segments[{idx}].script.between_tracks[{bt_idx}].text 必须是字符串或 null。",
                )
            if isinstance(text, str):
                if not _text_matches_language(text, language):
                    raise AIServiceError("segments[*].script.between_tracks[*].text 语言不一致。")

            sanitized_between_tracks.append(
                {"after_track_index": after_track_index, "text": text},
            )

        segment_patch: Dict[str, Any] = {
            "script": {"segment_intro": segment_intro, "between_tracks": sanitized_between_tracks},
        }
        if "segment_id" in seg:
            segment_patch["segment_id"] = seg["segment_id"]
        sanitized_segments.append(segment_patch)

    return {"segments": sanitized_segments}


class ScriptWriterAgent:
    """v3.0 Script Writer Agent：为各段补齐 segments[*].script。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._llm = llm_client or get_default_llm_client(self._settings)

    def run(
        self,
        state: PlanState,
        mode: str = "generation",
        *,
        audit_sink: PlanAuditSink | None = None,
        round_iteration: int | None = None,
        orchestration_mode: str = "legacy",
        audit_stage: str | None = None,
        audit_revision: int | None = None,
    ) -> PlanState:
        segments_count = len(state.get("segments", []))

        orch = (orchestration_mode or "legacy").strip().lower()
        if orch == "staged":
            messages = build_script_writer_staged_messages(state, mode)
        else:
            messages = build_script_writer_agent_messages(state, mode)

        gen_kwargs: Dict[str, Any] = {"temperature": 0.4}
        structured = should_use_structured_output(self._settings.llm)
        if structured:
            gen_kwargs["response_format"] = build_openrouter_response_format(
                "podcast_script_writer_response",
                build_script_writer_response_schema(segments_count),
            )

        try:
            raw = self._llm.generate(messages, **gen_kwargs)
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"调用 Script Writer Agent 失败：{exc!r}") from exc

        logger.debug("Script Writer Agent raw output (truncated): %s", raw[:1000])

        data = parse_agent_json_response(
            agent_label=AGENT_LABEL,
            raw=raw,
            state=state,
            structured=structured,
            allow_repair_fallback=not structured,
            output_dir=Path(self._settings.app.output_dir),
        )

        if audit_sink is not None and round_iteration is not None:
            audit_sink.write_agent_artifact(
                iteration=round_iteration,
                agent_slug=AUDIT_SLUG_SCRIPT_WRITER,
                mode=mode,
                request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                raw_llm_text=raw,
                parsed_patch=data,
                stage=audit_stage,
                revision=audit_revision,
            )

        language = str(state.get("meta", {}).get("language") or "")
        patch = _sanitize_script_writer_patch(
            data,
            expected_segments=segments_count,
            language=language,
        )

        next_state = merge_plan_state(state, patch)
        next_state = merge_plan_state(
            next_state,
            {"control": {"last_updated_by": "Script Writer"}},
        )
        return next_state

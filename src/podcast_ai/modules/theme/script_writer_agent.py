"""v7.3 Script Writer：模型输出定长 script_before；系统 mapper 回填既有 script schema。"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import Settings, load_settings, should_use_structured_output
from podcast_ai.infra.llm_client import LLMClient, get_default_llm_client
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme.agent_output_retry import run_generate_parse_sanitize
from podcast_ai.modules.theme.agent_response_schemas import (
    build_openrouter_response_format,
    build_script_writer_response_schema,
)
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_SCRIPT_WRITER, PlanAuditSink
from podcast_ai.modules.theme.prompts import build_script_writer_agent_messages
from podcast_ai.modules.theme.prompts_staged import build_script_writer_staged_messages
from podcast_ai.modules.theme.script_flat_mapper import (
    apply_revision_preserve,
    build_flat_tracks,
    flatten_script_before_from_state,
    map_script_before_to_segments_patch,
    normalize_cue_text,
    parse_action_episode_indices,
    total_track_count,
)
from podcast_ai.modules.theme.state import PlanState, merge_plan_state

logger = logging.getLogger(__name__)

AGENT_LABEL = "Script Writer Agent"

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


def sanitize_script_before_patch(
    data: Dict[str, Any],
    *,
    expected_tracks: int,
    language: str,
) -> Dict[str, Any]:
    """
    v7.3：仅接受顶层 ``script_before``；空串规范为 null。
    **不**写 segments[*].script（交给 mapper）。
    """
    if set(data.keys()) - {"script_before"}:
        forbidden = sorted(set(data.keys()) - {"script_before"})
        raise AIServiceError(f"Script Writer 越权写入顶层字段：{', '.join(forbidden)}。")

    raw_list = data.get("script_before")
    if not isinstance(raw_list, list):
        raise AIServiceError("Script Writer 输出中 script_before 必须是数组。")
    if len(raw_list) != expected_tracks:
        raise AIServiceError(
            f"script_before 长度必须 = N={expected_tracks}（收到 {len(raw_list)}）。",
        )

    cues: List[str | None] = []
    for idx, item in enumerate(raw_list):
        if item is not None and not isinstance(item, str):
            raise AIServiceError(f"script_before[{idx}] 必须是 string 或 null。")
        text = normalize_cue_text(item)
        if isinstance(text, str) and not _text_matches_language(text, language):
            raise AIServiceError(f"script_before[{idx}] 语言不一致。")
        cues.append(text)
    return {"script_before": cues}


class ScriptWriterAgent:
    """v7.3 Script Writer：扁平 script_before → 系统映射 segments[*].script。"""

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
        segments = state.get("segments") or []
        track_count = total_track_count(segments)
        if track_count < 1:
            raise AIServiceError("Script Writer：整期 playlist 总曲目 N=0，无法写串词。")

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
                build_script_writer_response_schema(track_count),
            )

        language = str(state.get("meta", {}).get("language") or "")
        mode_norm = (mode or "").strip().lower()
        flat_tracks = build_flat_tracks(segments)
        previous_cues = flatten_script_before_from_state(state)
        writer_actions = [
            a
            for a in (state.get("critic") or {}).get("actions", [])
            if isinstance(a, dict) and a.get("target_agent") == "Script Writer"
        ]
        mutable_idxs = parse_action_episode_indices(writer_actions, flat_tracks)

        # 成功路径审计用：扁平 sanitize 结果 + 映射后 patch
        last_flat: Dict[str, Any] | None = None
        last_mapped: Dict[str, Any] | None = None

        def _generate() -> str:
            try:
                return self._llm.generate(messages, **gen_kwargs)
            except AIServiceError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AIServiceError(f"调用 Script Writer Agent 失败：{exc!r}") from exc

        def _parse(raw: str) -> Dict[str, Any]:
            logger.debug("Script Writer Agent raw output (truncated): %s", raw[:1000])
            return parse_agent_json_response(
                agent_label=AGENT_LABEL,
                raw=raw,
                state=state,
                structured=structured,
                allow_repair_fallback=not structured,
                output_dir=Path(self._settings.app.output_dir),
            )

        def _sanitize_and_map(data: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal last_flat, last_mapped
            flat_patch = sanitize_script_before_patch(
                data,
                expected_tracks=track_count,
                language=language,
            )
            cues: List[str | None] = list(flat_patch["script_before"])
            # revision：未点名下标与上一轮落盘一致
            if mode_norm == "revision" and len(previous_cues) == track_count:
                cues = apply_revision_preserve(cues, previous_cues, mutable_idxs)
                flat_patch = {"script_before": cues}
            mapped = map_script_before_to_segments_patch(cues, segments)
            last_flat = flat_patch
            last_mapped = mapped
            return mapped

        def _on_fail(raw: str, stage: str, exc: BaseException) -> None:  # noqa: ARG001
            if audit_sink is None or round_iteration is None:
                return
            audit_sink.write_agent_artifact(
                iteration=round_iteration,
                agent_slug=AUDIT_SLUG_SCRIPT_WRITER,
                mode=mode,
                request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                raw_llm_text=raw,
                parsed_patch=None,
                stage=audit_stage,
                revision=audit_revision,
            )

        result = run_generate_parse_sanitize(
            agent_label=AGENT_LABEL,
            extra_retries=self._settings.llm.output_max_retries,
            generate=_generate,
            parse=_parse,
            sanitize=_sanitize_and_map,
            on_attempt_failed=_on_fail,
        )

        if audit_sink is not None and round_iteration is not None:
            audit_sink.write_agent_artifact(
                iteration=round_iteration,
                agent_slug=AUDIT_SLUG_SCRIPT_WRITER,
                mode=mode,
                request_id=str((state.get("meta") or {}).get("request_id") or "unknown"),
                raw_llm_text=result.raw,
                parsed_patch=last_flat if last_flat is not None else result.parsed,
                stage=audit_stage,
                revision=audit_revision,
                mapped_patch=last_mapped if last_mapped is not None else result.sanitized,
            )

        next_state = merge_plan_state(state, result.sanitized)
        next_state = merge_plan_state(
            next_state,
            {"control": {"last_updated_by": "Script Writer"}},
        )
        return next_state

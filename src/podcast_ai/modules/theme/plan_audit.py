"""
v3.6：PlanOrchestrator / 四 Agent 的可审计落盘（multi-agent 专用）。

单文件字段约定（iteration{i}_{agent}.json）：
  iteration, agent_slug, mode, request_id, raw_llm_text, parsed_patch, ts_utc

iteration{i}_state.json：根对象即完整 PlanState，与内存 state 同构（utf-8, indent=2, ensure_ascii=False）。

写盘失败仅打日志，不抛出，以免与 LLM/schema 错误混淆；可选严格模式未实现。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from podcast_ai.infra.storage.paths import (
    format_audit_agent_filename,
    format_audit_state_filename,
    format_audit_state_partial_filename,
)
from podcast_ai.modules.theme.state import PlanState

logger = logging.getLogger(__name__)

# 与 PRD 可识别角色名一致（文件名 slug）
AUDIT_SLUG_PLANNER = "planner"
AUDIT_SLUG_MUSIC_CURATOR = "music_curator"
AUDIT_SLUG_SCRIPT_WRITER = "script_writer"
AUDIT_SLUG_CRITIC = "critic"


class PlanAuditSink(Protocol):
    """构造注入到 Agent / Orchestrator；关闭审计时传 None。"""

    def write_agent_artifact(
        self,
        *,
        iteration: int,
        agent_slug: str,
        mode: str | None,
        request_id: str,
        raw_llm_text: str,
        parsed_patch: dict[str, Any],
    ) -> None: ...

    def write_state_snapshot(self, *, round_iteration: int, state: PlanState) -> None: ...

    def write_state_partial(
        self,
        *,
        round_iteration: int,
        state_before_round: PlanState,
        error_message: str,
    ) -> None: ...


@dataclass(frozen=True)
class FilePlanAuditSink:
    """将审计文件写入 ``run_dir``（目录会在首次写入时创建）。"""

    run_dir: Path

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_agent_artifact(
        self,
        *,
        iteration: int,
        agent_slug: str,
        mode: str | None,
        request_id: str,
        raw_llm_text: str,
        parsed_patch: dict[str, Any],
    ) -> None:
        path = self.run_dir / format_audit_agent_filename(iteration, agent_slug)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "iteration": iteration,
            "agent_slug": agent_slug,
            "mode": mode,
            "request_id": request_id,
            "raw_llm_text": raw_llm_text,
            "parsed_patch": parsed_patch,
            "ts_utc": ts,
        }
        try:
            self._write_json(path, payload)
        except OSError as exc:
            logger.error(
                "plan audit write_agent_artifact failed path=%s request_id=%s: %s",
                path,
                request_id,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "plan audit write_agent_artifact unexpected error path=%s request_id=%s: %s",
                path,
                request_id,
                exc,
            )

    def write_state_snapshot(self, *, round_iteration: int, state: PlanState) -> None:
        path = self.run_dir / format_audit_state_filename(round_iteration)
        rid = str((state.get("meta") or {}).get("request_id") or "unknown")
        try:
            self._write_json(path, state)
        except OSError as exc:
            logger.error(
                "plan audit write_state_snapshot failed path=%s request_id=%s: %s",
                path,
                rid,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "plan audit write_state_snapshot unexpected error path=%s request_id=%s: %s",
                path,
                rid,
                exc,
            )

    def write_state_partial(
        self,
        *,
        round_iteration: int,
        state_before_round: PlanState,
        error_message: str,
    ) -> None:
        """本轮某步失败时的可观测快照（见 paths.format_audit_state_partial_filename）。"""
        path = self.run_dir / format_audit_state_partial_filename(round_iteration)
        rid = str((state_before_round.get("meta") or {}).get("request_id") or "unknown")
        payload = {
            "partial": True,
            "round_iteration": round_iteration,
            "error_message": error_message,
            "state_before_round": state_before_round,
        }
        try:
            self._write_json(path, payload)
        except OSError as exc:
            logger.error(
                "plan audit write_state_partial failed path=%s request_id=%s: %s",
                path,
                rid,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "plan audit write_state_partial unexpected error path=%s request_id=%s: %s",
                path,
                rid,
                exc,
            )

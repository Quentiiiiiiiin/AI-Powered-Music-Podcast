"""
v3.6：PlanOrchestrator / 四 Agent 的可审计落盘（multi-agent 专用）。

单文件字段约定（iteration{i}_{agent}.json）：
  iteration, agent_slug, mode, request_id, raw_llm_text, parsed_patch, ts_utc

iteration{i}_state.json：根对象即完整 PlanState，与内存 state 同构（utf-8, indent=2, ensure_ascii=False）。

v6.8：写出 state_partial 后，同目录再写一份由 partial 内 state 派生的可消费 snapshot
（staged：``stage_{stage}_rev{r}_snapshot.json``；legacy：``iteration{i}_snapshot.json``）。
派生/写盘失败只打日志，不抛出，以免淹没原业务错误。

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
    build_episode_snapshot_from_state,
    format_audit_agent_filename,
    format_audit_failure_snapshot_filename,
    format_audit_staged_agent_filename,
    format_audit_staged_failure_snapshot_filename,
    format_audit_staged_state_filename,
    format_audit_staged_state_partial_filename,
    format_audit_state_filename,
    format_audit_state_partial_filename,
)
from podcast_ai.modules.theme.state import (
    PlanState,
    ensure_segment_snapshot_defaults,
    validate_episode_snapshot_subset,
)

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
        stage: str | None = None,
        revision: int | None = None,
    ) -> None: ...

    def write_state_snapshot(
        self,
        *,
        round_iteration: int,
        state: PlanState,
        stage: str | None = None,
        revision: int | None = None,
    ) -> None: ...

    def write_state_partial(
        self,
        *,
        round_iteration: int,
        state_before_round: PlanState,
        error_message: str,
        stage: str | None = None,
        revision: int | None = None,
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
        stage: str | None = None,
        revision: int | None = None,
    ) -> None:
        # v6.0：若传 stage，使用 stage_*_rev*_*.json；否则保持 legacy iteration 命名
        if stage is not None:
            path = self.run_dir / format_audit_staged_agent_filename(
                stage, int(revision or 0), agent_slug
            )
        else:
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
        if stage is not None:
            payload["stage"] = stage
            payload["revision"] = int(revision or 0)
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

    def write_state_snapshot(
        self,
        *,
        round_iteration: int,
        state: PlanState,
        stage: str | None = None,
        revision: int | None = None,
    ) -> None:
        if stage is not None:
            path = self.run_dir / format_audit_staged_state_filename(stage, int(revision or 0))
        else:
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
        stage: str | None = None,
        revision: int | None = None,
    ) -> None:
        """本轮某步失败时的可观测快照；并旁路写可消费 episode snapshot（v6.8）。"""
        if stage is not None:
            path = self.run_dir / format_audit_staged_state_partial_filename(
                stage, int(revision or 0)
            )
            snap_path = self.run_dir / format_audit_staged_failure_snapshot_filename(
                stage, int(revision or 0)
            )
        else:
            path = self.run_dir / format_audit_state_partial_filename(round_iteration)
            snap_path = self.run_dir / format_audit_failure_snapshot_filename(round_iteration)
        rid = str((state_before_round.get("meta") or {}).get("request_id") or "unknown")
        payload = {
            "partial": True,
            "round_iteration": round_iteration,
            "error_message": error_message,
            "state_before_round": state_before_round,
        }
        if stage is not None:
            payload["stage"] = stage
            payload["revision"] = int(revision or 0)
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
            # partial 写失败时仍尝试 snapshot（尽力而为）

        self._try_write_failure_snapshot(snap_path, state_before_round, request_id=rid)

    def _try_write_failure_snapshot(
        self,
        snap_path: Path,
        state_before_round: PlanState,
        *,
        request_id: str,
    ) -> None:
        """由 partial 内 state 派生 snapshot；失败只记日志，不抛错。"""
        try:
            ready = ensure_segment_snapshot_defaults(state_before_round)
            snapshot = build_episode_snapshot_from_state(ready)
            validate_episode_snapshot_subset(snapshot)
            self._write_json(snap_path, snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "plan audit failure-path snapshot skipped path=%s request_id=%s: %s",
                snap_path,
                request_id,
                exc,
            )

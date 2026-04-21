from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.modules.theme.json_repair import (
    dump_json_repair_debug,
    repair_and_standardize_json,
    standardize_llm_json_text,
)
from podcast_ai.modules.theme.state import PlanState

logger = logging.getLogger(__name__)


def parse_agent_json_response(
    *,
    agent_label: str,
    raw: str,
    state: PlanState,
    structured: bool,
    allow_repair_fallback: bool,
    output_dir: Path,
) -> dict[str, Any]:
    """
    v3.5 统一 JSON 解析入口（四个 Agent 公共逻辑）。

    解析策略：
    - structured=True（OpenRouter strict 主路径）：禁用 repair，只做轻量 standardize 后直接 json.loads
      （原因：strict schema 已在请求体侧约束，主路径的“成功率/可维护性”优先；repair 易掩盖契约问题）
    - structured=False：先尝试 json.loads；仅在失败且 allow_repair_fallback=True 时调用 repair_and_standardize_json 再解析

    返回保证：
    - 顶层 JSON 必须是对象 dict，否则抛 AIServiceError
    """
    req_id = str((state.get("meta") or {}).get("request_id") or "unknown")
    iter_s = str((state.get("control") or {}).get("iteration") or "na")

    standardized = standardize_llm_json_text(raw)

    def _loads_as_dict(text: str) -> dict[str, Any]:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise AIServiceError(f"{agent_label} JSON 顶层不是对象（dict）。")
        return data

    try:
        return _loads_as_dict(standardized)
    except Exception as exc:  # noqa: BLE001
        # structured 主路径禁用 repair：只要 json.loads/顶层类型检查失败就直接报错。
        if structured or not allow_repair_fallback:
            dump_info = dump_json_repair_debug(
                output_dir=output_dir,
                agent_name=agent_label,
                state=state,
                raw=raw,
                repaired=standardized,
                exc=exc,
            )
            logger.error(
                "%s JSON 解析失败（structured=%s）request_id=%s iteration=%s；debug=%s",
                agent_label,
                structured,
                req_id,
                iter_s,
                dump_info.dump_path,
            )
            raise AIServiceError(
                f"{agent_label} JSON解析失败（request_id={req_id}, iteration={iter_s}）：{exc}",
            ) from exc

        # 非 structured：json.loads 失败才触发 repair兜底（避免 repair 掩盖格式问题）。
        repaired = repair_and_standardize_json(raw)
        try:
            return _loads_as_dict(repaired)
        except Exception as exc2:  # noqa: BLE001
            dump_info = dump_json_repair_debug(
                output_dir=output_dir,
                agent_name=agent_label,
                state=state,
                raw=raw,
                repaired=repaired,
                exc=exc2,
            )
            logger.error(
                "%s JSON 解析失败（repair fallback）request_id=%s iteration=%s；debug=%s",
                agent_label,
                req_id,
                iter_s,
                dump_info.dump_path,
            )
            raise AIServiceError(
                f"{agent_label} JSON解析失败（request_id={req_id}, iteration={iter_s}）：{exc2}",
            ) from exc2


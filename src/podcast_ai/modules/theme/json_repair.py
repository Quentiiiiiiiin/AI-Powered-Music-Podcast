from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


_SMART_QUOTE_MAP: dict[str, str] = {
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
}


def standardize_llm_json_text(raw: str) -> str:
    """
    将常见 LLM raw 文本标准化为“更像 JSON”的文本：
    - 去除 code fence（```json / ```）
    - 替换智能引号（smart quotes）
    - 清理前后空白
    """
    text = (raw or "").strip()
    for k, v in _SMART_QUOTE_MAP.items():
        text = text.replace(k, v)

    # 去掉 code fence 包裹：```json ... ``` / ``` ... ```
    # 注意：不假设 fence 一定位于首行，尽量全局替换。
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    return text.strip()


def _extract_first_json_substring(text: str) -> Optional[str]:
    """
    从文本中提取第一个 JSON 对象/数组子串。
    做最小可用实现：使用括号/方括号栈，并尽量处理字符串状态。
    """
    s = text
    n = len(s)
    i = 0

    def is_start(ch: str) -> bool:
        return ch == "{" or ch == "["

    stack: list[str] = []
    in_string = False
    escape = False
    start_idx: Optional[int] = None

    while i < n:
        ch = s[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        # not in string
        if ch == '"':
            in_string = True
            i += 1
            continue

        if not stack:
            if is_start(ch):
                start_idx = i
                stack.append(ch)
            i += 1
            continue

        # stack not empty
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            top = stack[-1]
            if top == "{" and ch == "}":
                stack.pop()
            elif top == "[" and ch == "]":
                stack.pop()
            else:
                # 括号不匹配：放弃当前尝试，继续向后找下一个起点
                stack.clear()
                start_idx = None
        if not stack and start_idx is not None:
            return s[start_idx : i + 1]

        i += 1

    return None


def repair_and_standardize_json(raw: str) -> str:
    """
    在 standardize 的基础上做最小“可解析 JSON”修复：
    - 提取第一个 JSON 对象/数组子串（去掉前后文本）
    - 移除尾随逗号：`{...,}` / `[...,]`
    - 如果无法提取，退化为对标准化文本直接做尾随逗号移除

    注意：即使修复失败，也不抛异常；调用方应在 `json.loads` 处决定是否报错。
    """
    text = standardize_llm_json_text(raw)

    extracted = _extract_first_json_substring(text)
    if extracted is not None:
        text = extracted

    # 去掉对象/数组的尾随逗号（常见：{"a":1,}）
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


@dataclass(frozen=True)
class JsonRepairDebugInfo:
    agent_name: str
    dump_path: Path
    error_type: str


def dump_json_repair_debug(
    *,
    output_dir: Path,
    agent_name: str,
    state: dict[str, Any] | None,
    raw: str,
    repaired: str,
    exc: BaseException,
) -> JsonRepairDebugInfo:
    """
    保存 debug dump，便于定位 LLM 原始输出和修复后的文本。
    """
    meta = (state or {}).get("meta") or {}
    control = (state or {}).get("control") or {}
    request_id = str(meta.get("request_id") or "unknown")
    iteration = str(control.get("iteration") or "na")

    out_dir = Path(output_dir) / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_agent = (agent_name or "agent").strip().lower().replace(" ", "_")
    dump_path = out_dir / f"{safe_agent}_jsondecode_error_{request_id}_iter{iteration}_{ts}.json"

    payload = {
        "agent": agent_name,
        "error": {"type": type(exc).__name__, "message": str(exc)},
        "raw": raw,
        "repaired": repaired,
    }
    dump_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return JsonRepairDebugInfo(agent_name=agent_name, dump_path=dump_path, error_type=type(exc).__name__)


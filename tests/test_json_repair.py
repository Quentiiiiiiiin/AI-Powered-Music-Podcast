from __future__ import annotations

import json

from podcast_ai.modules.theme.json_repair import repair_and_standardize_json, standardize_llm_json_text


def test_standardize_llm_json_text_removes_code_fence_and_smart_quotes() -> None:
    raw = "```json\n{“a”: 1,}\n```"
    standardized = standardize_llm_json_text(raw)
    # 应去掉 code fence，并把 smart quotes 变回普通引号
    assert "```" not in standardized
    assert "“" not in standardized
    assert "”" not in standardized


def test_repair_and_standardize_json_can_be_parsed() -> None:
    raw = "这里是结果：```json\n{\"a\": 1,}\n``` 其他说明"
    repaired = repair_and_standardize_json(raw)
    assert json.loads(repaired) == {"a": 1}


def test_repair_and_standardize_json_extracts_first_array() -> None:
    raw = "前置文本 [{\"x\": 1,}], 尾随文本"
    repaired = repair_and_standardize_json(raw)
    assert json.loads(repaired) == [{"x": 1}]


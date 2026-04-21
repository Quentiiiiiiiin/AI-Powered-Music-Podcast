from __future__ import annotations

import json

import pytest

from podcast_ai.modules.theme.json_repair import (
    close_unclosed_json_structures,
    repair_and_standardize_json,
    standardize_llm_json_text,
)


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


def test_close_unclosed_json_structures_object_and_array() -> None:
    assert json.loads(close_unclosed_json_structures('{"a":1')) == {"a": 1}
    assert json.loads(close_unclosed_json_structures("[1,2")) == [1, 2]
    nested = '{"a":[1,2'
    assert json.loads(close_unclosed_json_structures(nested)) == {"a": [1, 2]}


def test_close_unclosed_json_structures_respects_strings_and_escapes() -> None:
    # 引号内的 { 不计入结构；末尾缺 }
    s = r'{"k": "brace:{ not counted"}'
    assert json.loads(close_unclosed_json_structures(s)) == {"k": "brace:{ not counted"}
    # 转义引号不结束字符串
    # JSON 串内转义引号，不应提前结束字符串解析
    t = '{"k": "a\\"b", "n": 1'
    assert json.loads(close_unclosed_json_structures(t)) == {"k": 'a"b', "n": 1}


def test_close_unclosed_json_structures_mismatch_returns_original() -> None:
    assert close_unclosed_json_structures("}") == "}"
    assert close_unclosed_json_structures("{]") == "{]"
    assert close_unclosed_json_structures("[}") == "[}"


def test_close_unclosed_json_structures_unclosed_string_returns_original() -> None:
    # 值侧引号已打开但未闭合：不追加 } / ]
    assert close_unclosed_json_structures('{"a":"') == '{"a":"'


def test_repair_pipeline_truncated_json_with_prefix() -> None:
    raw = '说明文字 {"a": 1, "b": [true'
    repaired = repair_and_standardize_json(raw)
    assert json.loads(repaired) == {"a": 1, "b": [True]}


def test_repair_pipeline_v32_regression_unchanged() -> None:
    raw = "这里是结果：```json\n{\"a\": 1,}\n``` 其他说明"
    assert json.loads(repair_and_standardize_json(raw)) == {"a": 1}
    raw2 = "前置文本 [{\"x\": 1,}], 尾随文本"
    assert json.loads(repair_and_standardize_json(raw2)) == [{"x": 1}]


def test_repair_pipeline_illegal_still_fails_json_loads() -> None:
    with pytest.raises(json.JSONDecodeError):
        json.loads(repair_and_standardize_json("{not-valid}"))


def test_repair_pipeline_single_open_brace_closes_to_empty_object() -> None:
    """仅有一个 `{` 时补全为 `{}`，属于 v3.3 末尾闭合的边界行为。"""
    assert json.loads(repair_and_standardize_json("{")) == {}


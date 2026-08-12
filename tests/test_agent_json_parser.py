from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.modules.theme.agent_json_parser import parse_agent_json_response
from podcast_ai.modules.theme import agent_json_parser as parser_mod
from podcast_ai.modules.theme.state import initialize_plan_state


def _request(tmp_path: Path) -> EpisodeRequest:
    return EpisodeRequest(
        topic="T",
        duration_minutes=60,
        language="zh",
        output_dir=tmp_path,
    )


def test_parse_agent_json_response_structured_true_no_repair_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = initialize_plan_state(_request(tmp_path))
    calls: list[str] = []

    def _repair_should_not_be_called(raw: str) -> str:  # noqa: ARG001
        calls.append("called")
        raise AssertionError("repair_and_standardize_json must not be called in structured=True main path")

    monkeypatch.setattr(parser_mod, "repair_and_standardize_json", _repair_should_not_be_called)

    out = parse_agent_json_response(
        agent_label="TestAgent",
        raw='{"x":1}',
        state=state,
        structured=True,
        allow_repair_fallback=False,
        output_dir=tmp_path,
    )
    assert out == {"x": 1}
    assert not calls


def test_parse_agent_json_response_structured_true_invalid_json_raises_with_request_and_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = initialize_plan_state(_request(tmp_path))

    def _repair_should_not_be_called(raw: str) -> str:  # noqa: ARG001
        raise AssertionError("repair_and_standardize_json must not be called in structured=True main path")

    monkeypatch.setattr(parser_mod, "repair_and_standardize_json", _repair_should_not_be_called)

    with pytest.raises(AIServiceError) as exc_info:
        parse_agent_json_response(
            agent_label="TestAgent",
            raw="{not-valid",
            state=state,
            structured=True,
            allow_repair_fallback=False,
            output_dir=tmp_path,
        )

    msg = str(exc_info.value)
    assert "TestAgent" in msg
    assert str(state["meta"]["request_id"]) in msg
    assert str(state["control"]["iteration"]) in msg


def test_parse_agent_json_response_non_structured_repair_fallback_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = initialize_plan_state(_request(tmp_path))

    original = parser_mod.repair_and_standardize_json
    calls: list[Any] = []

    def _repair_wrapper(raw: str) -> str:  # noqa: ANN001
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(parser_mod, "repair_and_standardize_json", _repair_wrapper)

    out = parse_agent_json_response(
        agent_label="TestAgent",
        raw='前置文本 {"a":1,} 尾随文本',
        state=state,
        structured=False,
        allow_repair_fallback=True,
        output_dir=tmp_path,
    )
    assert out == {"a": 1}
    assert calls, "expected repair fallback to be invoked"


def test_parse_agent_json_response_prefers_raw_before_standardize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = initialize_plan_state(_request(tmp_path))
    calls: list[str] = []

    def _repair_should_not_be_called(raw: str) -> str:  # noqa: ARG001
        calls.append("called")
        raise AssertionError("repair_and_standardize_json should not be called for valid raw JSON")

    monkeypatch.setattr(parser_mod, "repair_and_standardize_json", _repair_should_not_be_called)

    # 该文本中的 “元歌曲” 在 JSON 中是合法字符；若先标准化替换引号会被破坏。
    raw = '{"text":"而《土耳其冰淇淋》是一首关于音乐本身的“元歌曲”，很有趣。"}'
    out = parse_agent_json_response(
        agent_label="TestAgent",
        raw=raw,
        state=state,
        structured=True,
        allow_repair_fallback=False,
        output_dir=tmp_path,
    )
    assert out["text"].endswith("很有趣。")
    assert not calls


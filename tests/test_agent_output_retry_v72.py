"""v7.2：输出校验重试 — 与 HTTP retry 分计；失败尝试审计 raw。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings
from podcast_ai.infra.storage.paths import format_audit_agent_filename
from podcast_ai.modules.theme.agent_output_retry import run_generate_parse_sanitize
from podcast_ai.modules.theme.plan_audit import AUDIT_SLUG_PLANNER, FilePlanAuditSink
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.state import initialize_plan_state


def test_output_max_retries_default_and_zero() -> None:
    cfg = LLMConfig()
    assert cfg.output_max_retries == 2
    assert LLMConfig(output_max_retries=0).output_max_retries == 0


def test_output_max_retries_rejects_negative() -> None:
    with pytest.raises(ValidationError, match="output_max_retries"):
        LLMConfig(output_max_retries=-1)


class _SeqGenerate:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        item = self.outputs[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return str(item)


def test_helper_retries_zero_parse_fail_once() -> None:
    gen = _SeqGenerate(["not-json"])
    fails: list[tuple[str, str]] = []

    def _parse(_raw: str) -> dict[str, Any]:
        raise AIServiceError("JSON 解析失败")

    with pytest.raises(AIServiceError, match="JSON"):
        run_generate_parse_sanitize(
            agent_label="X",
            extra_retries=0,
            generate=gen,
            parse=_parse,
            sanitize=lambda d: d,
            on_attempt_failed=lambda raw, stage, _exc: fails.append((raw, stage)),
        )
    assert gen.calls == 1
    assert fails == [("not-json", "parse")]


def test_helper_second_attempt_succeeds() -> None:
    gen = _SeqGenerate(["bad", '{"ok": true}'])
    parsed_ok = {"ok": True}

    def _parse(raw: str) -> dict[str, Any]:
        if raw == "bad":
            raise AIServiceError("JSON 解析失败")
        return json.loads(raw)

    result = run_generate_parse_sanitize(
        agent_label="X",
        extra_retries=2,
        generate=gen,
        parse=_parse,
        sanitize=lambda d: {**d, "s": 1},
    )
    assert gen.calls == 2
    assert result.parsed == parsed_ok
    assert result.sanitized == {"ok": True, "s": 1}


def test_helper_sanitize_fail_then_ok() -> None:
    gen = _SeqGenerate(['{"a": 1}', '{"a": 2}'])

    def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
        if data["a"] == 1:
            raise AIServiceError("sanitize 不合格")
        return data

    result = run_generate_parse_sanitize(
        agent_label="X",
        extra_retries=1,
        generate=gen,
        parse=lambda raw: json.loads(raw),
        sanitize=_sanitize,
    )
    assert gen.calls == 2
    assert result.sanitized == {"a": 2}


def test_helper_exhausted_generate_count() -> None:
    gen = _SeqGenerate(["a", "b", "c", "d"])

    with pytest.raises(AIServiceError, match="JSON"):
        run_generate_parse_sanitize(
            agent_label="X",
            extra_retries=2,
            generate=gen,
            parse=lambda _raw: (_ for _ in ()).throw(AIServiceError("JSON 解析失败")),
            sanitize=lambda d: d,
        )
    assert gen.calls == 3  # 1 + extra_retries


def test_helper_http_error_does_not_consume_output_retries() -> None:
    gen = _SeqGenerate([AIServiceError("HTTP 502")])
    with pytest.raises(AIServiceError, match="HTTP 502"):
        run_generate_parse_sanitize(
            agent_label="X",
            extra_retries=5,
            generate=gen,
            parse=lambda raw: json.loads(raw),
            sanitize=lambda d: d,
        )
    assert gen.calls == 1


def test_helper_failed_attempts_call_on_attempt_failed() -> None:
    gen = _SeqGenerate(["x", "y"])
    seen: list[str] = []

    with pytest.raises(AIServiceError):
        run_generate_parse_sanitize(
            agent_label="X",
            extra_retries=1,
            generate=gen,
            parse=lambda raw: (_ for _ in ()).throw(AIServiceError("bad")),
            sanitize=lambda d: d,
            on_attempt_failed=lambda raw, stage, _exc: seen.append(f"{stage}:{raw}"),
        )
    assert seen == ["parse:x", "parse:y"]


class _SeqLLM:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls = 0

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        self.calls += 1
        return self.texts[self.calls - 1]


def _settings(tmp: Path, extra: int) -> Settings:
    return Settings(
        app=AppConfig(output_dir=str(tmp)),
        llm=LLMConfig(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="m",
            structured_output=False,
            max_retries=9,
            output_max_retries=extra,
        ),
    )


def _planner_ok() -> dict[str, Any]:
    return {
        "meta": {
            "theme_description": "ok-theme",
            "theme_type": "",
            "theme_subject": "",
            "theme_relationship": "",
        },
        "global_constraints": None,
        "plan": None,
        "segments": None,
    }


def test_planner_output_retry_zero_fails_once(tmp_path: Path) -> None:
    llm = _SeqLLM(["NOT_JSON"])
    agent = PlannerAgent(llm_client=llm, settings=_settings(tmp_path, extra=0))
    state = initialize_plan_state(
        EpisodeRequest(topic="T", duration_minutes=60, language="zh", output_dir=tmp_path)
    )
    with pytest.raises(AIServiceError, match=r"JSON|解析"):
        agent.run(state)
    assert llm.calls == 1


def test_planner_second_generate_used_for_state(tmp_path: Path) -> None:
    llm = _SeqLLM(["NOT_JSON", json.dumps(_planner_ok(), ensure_ascii=False)])
    agent = PlannerAgent(llm_client=llm, settings=_settings(tmp_path, extra=2))
    state = initialize_plan_state(
        EpisodeRequest(topic="T", duration_minutes=60, language="zh", output_dir=tmp_path)
    )
    next_state = agent.run(state)
    assert llm.calls == 2
    assert next_state["meta"]["theme_description"] == "ok-theme"


def test_planner_failed_attempts_write_audit_raw(tmp_path: Path) -> None:
    llm = _SeqLLM(["RAW_FAIL_A", "RAW_FAIL_B"])
    settings = _settings(tmp_path, extra=1)
    agent = PlannerAgent(llm_client=llm, settings=settings)
    state = initialize_plan_state(
        EpisodeRequest(topic="T", duration_minutes=60, language="zh", output_dir=tmp_path)
    )
    rid = str(state["meta"]["request_id"])
    run_dir = tmp_path / "audit"
    sink = FilePlanAuditSink(run_dir)
    with pytest.raises(AIServiceError, match=r"JSON|解析"):
        agent.run(state, audit_sink=sink, round_iteration=4)
    assert llm.calls == 2
    path = run_dir / format_audit_agent_filename(4, AUDIT_SLUG_PLANNER)
    data = json.loads(path.read_text(encoding="utf-8"))
    # 同路径覆盖：最后一次失败原文仍在
    assert "RAW_FAIL_B" in data["raw_llm_text"]
    assert data["parsed_patch"] is None
    assert data["request_id"] == rid


class _HttpThenUnusedLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # noqa: ARG002
        self.calls += 1
        raise AIServiceError("LLM 服务端错误（HTTP 502）。")


def test_planner_http_error_skips_output_retry(tmp_path: Path) -> None:
    llm = _HttpThenUnusedLLM()
    agent = PlannerAgent(llm_client=llm, settings=_settings(tmp_path, extra=4))
    state = initialize_plan_state(
        EpisodeRequest(topic="T", duration_minutes=60, language="zh", output_dir=tmp_path)
    )
    with pytest.raises(AIServiceError, match="HTTP 502"):
        agent.run(state)
    assert llm.calls == 1

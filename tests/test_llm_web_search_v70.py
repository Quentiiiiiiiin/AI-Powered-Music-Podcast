"""v7.0：OpenRouter Server Tool `openrouter:web_search` 注入与配置。

手工验收（可选）：OpenRouter + llm.web_search.enabled=true 跑一次 plan，
DEBUG 日志应含 web_search=on；响应 usage 若有 server_tool_use.web_search_requests 会打 INFO。
禁止弃用路径：plugins web / model :online。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import (
    LLMConfig,
    WebSearchConfig,
    build_openrouter_web_search_tool,
    should_inject_web_search,
)
from podcast_ai.infra.llm_client import OpenAICompatibleLLMClient


def _install_capture(
    monkeypatch: pytest.MonkeyPatch,
    bodies: list[dict[str, Any]],
    *,
    response_json: dict[str, Any] | None = None,
) -> None:
    reply = response_json or {"choices": [{"message": {"content": "ok"}}]}

    def _build_client(self: OpenAICompatibleLLMClient) -> httpx.Client:
        timeout = httpx.Timeout(self._cfg.timeout_seconds)
        headers: dict[str, str] = {}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"

        def _capture(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=reply)

        return httpx.Client(
            base_url=self._cfg.base_url,
            transport=httpx.MockTransport(_capture),
            headers=headers,
            timeout=timeout,
        )

    monkeypatch.setattr(OpenAICompatibleLLMClient, "_build_client", _build_client)


def test_web_search_config_defaults() -> None:
    cfg = LLMConfig()
    assert cfg.web_search.enabled is False
    assert cfg.web_search.engine == "auto"
    assert cfg.web_search.max_results == 5
    assert cfg.web_search.max_uses is None


def test_web_search_config_rejects_bad_max_results() -> None:
    with pytest.raises(Exception):
        WebSearchConfig(max_results=0)
    with pytest.raises(Exception):
        WebSearchConfig(max_results=26)


def test_web_search_config_rejects_bad_engine() -> None:
    with pytest.raises(Exception):
        WebSearchConfig(engine="bing")


def test_should_inject_requires_openrouter_and_enabled() -> None:
    off = LLMConfig(
        base_url="https://openrouter.ai/api/v1",
        web_search=WebSearchConfig(enabled=False),
    )
    assert should_inject_web_search(off) is False
    non_or = LLMConfig(
        base_url="https://api.openai.com/v1",
        web_search=WebSearchConfig(enabled=True),
    )
    assert should_inject_web_search(non_or) is False
    on = LLMConfig(
        base_url="https://openrouter.ai/api/v1",
        web_search=WebSearchConfig(enabled=True, engine="exa", max_results=3, max_uses=2),
    )
    assert should_inject_web_search(on) is True
    tool = build_openrouter_web_search_tool(on)
    assert tool == {
        "type": "openrouter:web_search",
        "parameters": {"engine": "exa", "max_results": 3, "max_uses": 2},
    }


def test_openrouter_enabled_injects_web_search_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=True, engine="auto", max_results=5),
    )
    OpenAICompatibleLLMClient(cfg).generate([{"role": "user", "content": "hi"}])
    assert bodies
    tools = bodies[0].get("tools")
    assert isinstance(tools, list)
    assert any(
        isinstance(t, dict) and t.get("type") == "openrouter:web_search" for t in tools
    )
    ws = next(t for t in tools if t.get("type") == "openrouter:web_search")
    assert ws["parameters"]["engine"] == "auto"
    assert ws["parameters"]["max_results"] == 5
    assert "max_uses" not in ws["parameters"]
    assert "plugins" not in bodies[0]
    assert ":online" not in str(bodies[0].get("model", ""))


def test_enabled_false_does_not_inject_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=False),
    )
    OpenAICompatibleLLMClient(cfg).generate([{"role": "user", "content": "hi"}])
    assert "tools" not in bodies[0]


def test_non_openrouter_host_does_not_inject(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://api.openai.com/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=True),
    )
    OpenAICompatibleLLMClient(cfg).generate([{"role": "user", "content": "hi"}])
    assert "tools" not in bodies[0]


def test_web_search_coexists_with_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=True, max_results=4),
    )
    rf = {
        "type": "json_schema",
        "json_schema": {"name": "x", "strict": True, "schema": {"type": "object"}},
    }
    OpenAICompatibleLLMClient(cfg).generate(
        [{"role": "user", "content": "hi"}],
        response_format=rf,
    )
    assert bodies[0]["response_format"]["type"] == "json_schema"
    assert any(t.get("type") == "openrouter:web_search" for t in bodies[0]["tools"])


def test_config_overrides_caller_duplicate_web_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置优先：调用方自带同类型 tool 时用配置参数替换。"""
    bodies: list[dict[str, Any]] = []
    _install_capture(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=True, engine="exa", max_results=7),
    )
    OpenAICompatibleLLMClient(cfg).generate(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "openrouter:web_search", "parameters": {"engine": "native", "max_results": 1}}],
    )
    ws_tools = [t for t in bodies[0]["tools"] if t.get("type") == "openrouter:web_search"]
    assert len(ws_tools) == 1
    assert ws_tools[0]["parameters"]["engine"] == "exa"
    assert ws_tools[0]["parameters"]["max_results"] == 7


def test_logs_web_search_requests_from_usage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture(
        monkeypatch,
        bodies,
        response_json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"server_tool_use": {"web_search_requests": 2}},
        },
    )
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=True),
    )
    with caplog.at_level("INFO"):
        OpenAICompatibleLLMClient(cfg).generate([{"role": "user", "content": "hi"}])
    assert any("web_search_requests=2" in r.message for r in caplog.records)


def test_missing_content_raises_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture(
        monkeypatch,
        bodies,
        response_json={"choices": [{"message": {}}], "usage": {"input_tokens": 1}},
    )
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        web_search=WebSearchConfig(enabled=False),
    )
    with pytest.raises(AIServiceError, match=r"content|choices"):
        OpenAICompatibleLLMClient(cfg).generate([{"role": "user", "content": "hi"}])


def test_source_has_no_deprecated_plugin_or_online_paths() -> None:
    """静态守卫：llm_client / config 不得构造 plugins web 或 :online 模型后缀。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "podcast_ai" / "infra"
    text = (root / "llm_client.py").read_text(encoding="utf-8") + (root / "config.py").read_text(
        encoding="utf-8"
    )
    assert "openrouter:web_search" in text
    # 不得主动写入弃用形态
    assert '"plugins"' not in text and "'plugins'" not in text
    assert 'id": "web"' not in text and "id': 'web'" not in text
    assert '":online"' not in text and "':online'" not in text
    assert "+ ':online'" not in text and '+" :online"' not in text

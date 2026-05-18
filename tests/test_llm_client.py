"""v4.6：OpenRouter `provider` 请求体与网关判定。"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.infra.config import LLMConfig
from podcast_ai.infra.llm_client import OpenAICompatibleLLMClient


def _install_capture_client(monkeypatch: pytest.MonkeyPatch, bodies: list[dict[str, Any]]) -> None:
    def _build_client(self: OpenAICompatibleLLMClient) -> httpx.Client:
        timeout = httpx.Timeout(self._cfg.timeout_seconds)
        headers: dict[str, str] = {}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"

        def _capture(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        return httpx.Client(
            base_url=self._cfg.base_url,
            transport=httpx.MockTransport(_capture),
            headers=headers,
            timeout=timeout,
        )

    monkeypatch.setattr(OpenAICompatibleLLMClient, "_build_client", _build_client)


def test_v46_openrouter_empty_openrouter_provider_omits_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture_client(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        openrouter_provider="",
    )
    client = OpenAICompatibleLLMClient(cfg)
    client.generate([{"role": "user", "content": "hi"}])
    assert bodies
    assert "provider" not in bodies[0]


def test_v46_openrouter_non_empty_slug_sets_only_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture_client(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        openrouter_provider="anthropic",
    )
    client = OpenAICompatibleLLMClient(cfg)
    client.generate([{"role": "user", "content": "hi"}])
    assert bodies[0]["provider"] == {"only": ["anthropic"]}


def test_v46_openrouter_json_object_provider_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture_client(monkeypatch, bodies)
    want = {"order": ["openai", "anthropic"], "allow_fallbacks": False}
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        openrouter_provider=json.dumps(want, ensure_ascii=False),
    )
    client = OpenAICompatibleLLMClient(cfg)
    client.generate([{"role": "user", "content": "hi"}])
    assert bodies[0]["provider"] == want


def test_v46_non_openrouter_base_url_never_sends_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture_client(monkeypatch, bodies)
    cfg = LLMConfig(
        api_key="k",
        base_url="https://api.openai.com/v1",
        model="m",
        max_retries=1,
        openrouter_provider="anthropic",
    )
    client = OpenAICompatibleLLMClient(cfg)
    client.generate([{"role": "user", "content": "hi"}])
    assert "provider" not in bodies[0]


def test_v46_response_format_kwarg_preserved_with_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []
    _install_capture_client(monkeypatch, bodies)
    rf = {"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": {"type": "object"}}}
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        openrouter_provider="openai",
    )
    client = OpenAICompatibleLLMClient(cfg)
    client.generate([{"role": "user", "content": "hi"}], response_format=rf)
    assert bodies[0]["response_format"] == rf
    assert bodies[0]["provider"] == {"only": ["openai"]}


def test_v46_invalid_openrouter_provider_json_raises() -> None:
    cfg = LLMConfig(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        max_retries=1,
        openrouter_provider="{not-json",
    )
    client = OpenAICompatibleLLMClient(cfg)
    with pytest.raises(AIServiceError, match="openrouter_provider"):
        client.generate([{"role": "user", "content": "hi"}])


def test_v46_400_body_mentions_provider_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(400, text='{"error":"invalid provider for model"}'),
    )

    def _build_client(self: OpenAICompatibleLLMClient) -> httpx.Client:
        return httpx.Client(
            base_url=self._cfg.base_url,
            transport=transport,
            headers={"Authorization": "Bearer x"},
            timeout=httpx.Timeout(5),
        )

    monkeypatch.setattr(OpenAICompatibleLLMClient, "_build_client", _build_client)
    client = OpenAICompatibleLLMClient(
        LLMConfig(api_key="x", base_url="https://openrouter.ai/api/v1", model="m", max_retries=1),
    )
    with pytest.raises(AIServiceError, match="OpenRouter provider"):
        client.generate([{"role": "user", "content": "hi"}])

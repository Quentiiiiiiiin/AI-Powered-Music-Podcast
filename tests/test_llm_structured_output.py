"""v3.4：OpenRouter structured response_format 开关与请求体验证。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings, should_use_structured_output
from podcast_ai.infra.llm_client import OpenAICompatibleLLMClient
from podcast_ai.modules.theme.critic_agent import CriticAgent
from podcast_ai.modules.theme.music_curator_agent import MusicCuratorAgent
from podcast_ai.modules.theme.planner_agent import PlannerAgent
from podcast_ai.modules.theme.script_writer_agent import ScriptWriterAgent
from podcast_ai.modules.theme.llm_planner import ThemePlanner
from podcast_ai.modules.theme.state import initialize_plan_state


@pytest.mark.parametrize(
    ("base_url", "structured_flag", "expected"),
    [
        ("https://openrouter.ai/api/v1", None, True),
        ("https://OPENROUTER.ai/api/v1", None, True),
        ("https://api.openai.com/v1", None, False),
        ("https://api.openai.com/v1", True, True),
        ("https://openrouter.ai/api/v1", False, False),
        ("", None, False),
    ],
)
def test_should_use_structured_output(base_url: str, structured_flag: bool | None, expected: bool) -> None:
    cfg = LLMConfig(base_url=base_url, structured_output=structured_flag)
    assert should_use_structured_output(cfg) is expected


def _episode_request(tmp: Path) -> EpisodeRequest:
    return EpisodeRequest(
        topic="T",
        duration_minutes=60,
        language="zh",
        output_dir=tmp,
    )


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    transport = httpx.MockTransport(handler)

    def _build_client(self: OpenAICompatibleLLMClient) -> httpx.Client:
        timeout = httpx.Timeout(self._cfg.timeout_seconds)
        headers: dict[str, str] = {}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        return httpx.Client(
            base_url=self._cfg.base_url or "",
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    monkeypatch.setattr(OpenAICompatibleLLMClient, "_build_client", _build_client)


def test_openrouter_posts_response_format_for_planner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bodies: list[dict[str, Any]] = []

    planner_reply = {
        "meta": None,
        "global_constraints": None,
        "plan": None,
        "segments": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(planner_reply, ensure_ascii=False)}}]},
        )

    _install_mock_transport(monkeypatch, handler)

    llm_cfg = LLMConfig(
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        model="test/model",
    )
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm_cfg)
    state = initialize_plan_state(_episode_request(tmp_path))
    agent = PlannerAgent(llm_client=OpenAICompatibleLLMClient(llm_cfg), settings=settings)
    agent.run(state)

    assert len(bodies) == 1
    rf = bodies[0].get("response_format")
    assert rf is not None
    assert rf.get("type") == "json_schema"
    assert rf.get("json_schema", {}).get("strict") is True
    assert rf.get("json_schema", {}).get("name") == "podcast_planner_response"
    assert rf.get("json_schema", {}).get("schema", {}).get("properties", {}).get("segments") is not None


def test_non_openrouter_no_response_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"meta": None, "global_constraints": None, "plan": None, "segments": None})}},
                ],
            },
        )

    _install_mock_transport(monkeypatch, handler)

    llm_cfg = LLMConfig(
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        structured_output=False,
    )
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm_cfg)
    agent = PlannerAgent(llm_client=OpenAICompatibleLLMClient(llm_cfg), settings=settings)
    agent.run(state=initialize_plan_state(_episode_request(tmp_path)))

    assert "response_format" not in bodies[0]


def test_music_curator_response_format_name_and_segment_array_bounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        reply = {
            "segments": [
                {"segment_id": "seg_01", "playlist": [{"track": "A", "artist": "X", "bpm": 90}]},
            ]
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(reply)}}]},
        )

    _install_mock_transport(monkeypatch, handler)

    llm_cfg = LLMConfig(api_key="k", base_url="https://openrouter.ai/api/v1", model="m")
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm_cfg)
    state = initialize_plan_state(_episode_request(tmp_path))
    MusicCuratorAgent(llm_client=OpenAICompatibleLLMClient(llm_cfg), settings=settings).run(state)

    rf = bodies[0]["response_format"]
    assert rf["json_schema"]["name"] == "podcast_music_curator_response"
    seg_schema = rf["json_schema"]["schema"]["properties"]["segments"]
    assert seg_schema["minItems"] == 1
    assert seg_schema["maxItems"] == 1


def test_script_writer_response_format_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        reply = {
            "segments": [
                {
                    "segment_id": "seg_01",
                    "script": {
                        "segment_intro": "开场白含有汉字用于语言校验。",
                        "between_tracks": [{"after_track_index": 0, "text": None}],
                    },
                },
            ]
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]},
        )

    _install_mock_transport(monkeypatch, handler)

    llm_cfg = LLMConfig(api_key="k", base_url="https://openrouter.ai/api/v1", model="m")
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm_cfg)
    state = initialize_plan_state(_episode_request(tmp_path))
    ScriptWriterAgent(llm_client=OpenAICompatibleLLMClient(llm_cfg), settings=settings).run(state)

    assert bodies[0]["response_format"]["json_schema"]["name"] == "podcast_script_writer_response"


def test_critic_response_format_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bodies: list[dict[str, Any]] = []

    payload = {
        "critic": {
            "pass": True,
            "scores": {"coherence": 8, "emotion_flow": 8, "immersion": 8},
            "issues": [],
            "actions": [],
        },
        "control": {"next_agent": "Orchestrator"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    _install_mock_transport(monkeypatch, handler)

    llm_cfg = LLMConfig(api_key="k", base_url="https://openrouter.ai/api/v1", model="m")
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm_cfg)
    CriticAgent(llm_client=OpenAICompatibleLLMClient(llm_cfg), settings=settings).run(
        initialize_plan_state(_episode_request(tmp_path)),
    )

    assert bodies[0]["response_format"]["json_schema"]["name"] == "podcast_critic_response"
    assert bodies[0]["response_format"]["json_schema"]["schema"]["properties"]["critic"] is not None


def test_llm_client_400_hints_response_format_when_body_mentions_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(400, text='{"error":"invalid json_schema for model"}'),
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
    with pytest.raises(AIServiceError, match="response_format"):
        client.generate([{"role": "user", "content": "hi"}], response_format={"type": "json_schema"})


def test_single_agent_planner_posts_state_subset_response_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bodies: list[dict[str, Any]] = []

    reply = {
        "schema_version": "v3.0",
        "meta": {
            "request_id": "req-single",
            "theme": "T",
            "theme_description": "desc",
            "language": "zh-CN",
            "target_duration_seconds": 3600,
            "overall_bpm_range": [90, 120],
        },
        "global_constraints": {"tone": "克制", "language_style": "第一人称", "avoid": []},
        "plan": {"segments_design": "三段", "emotion_curve": ["平静", "抬升", "收束"]},
        "segments": [
            {
                "segment_id": "seg_01",
                "order": 1,
                "name": "开场",
                "target_duration_seconds": 1200,
                "bpm_range": [90, 104],
                "mood": "舒缓",
                "segment_design": "开场铺垫",
                "playlist": [{"track": "A", "artist": "X", "bpm": 96}],
                "script": {"segment_intro": "欢迎来到节目。", "between_tracks": [{"after_track_index": 0, "text": None}]},
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]},
        )

    _install_mock_transport(monkeypatch, handler)
    llm_cfg = LLMConfig(api_key="k", base_url="https://openrouter.ai/api/v1", model="m")
    settings = Settings(app=AppConfig(output_dir=str(tmp_path)), llm=llm_cfg)
    planner = ThemePlanner(llm_client=OpenAICompatibleLLMClient(llm_cfg), settings=settings)

    state = planner.generate_plan_state(_episode_request(tmp_path), agent_mode="single_agent")

    rf = bodies[0]["response_format"]
    assert rf["json_schema"]["name"] == "podcast_single_agent_state_subset"
    top_keys = set(rf["json_schema"]["schema"]["properties"].keys())
    assert top_keys == {"schema_version", "meta", "global_constraints", "plan", "segments"}
    assert set(state.keys()) == {"schema_version", "meta", "global_constraints", "plan", "segments"}

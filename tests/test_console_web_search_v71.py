"""v7.1 Task 05：Console web_search 参数合并、日志解析、Preset 往返轻量回归。"""
from __future__ import annotations

from pathlib import Path

from podcast_ai.console.presets import PRESET_KEYS, build_snapshot, load_preset, save_preset
from podcast_ai.console.run_progress import parse_plan_progress_from_logs
from podcast_ai.console.runner import ConsoleParams, defaults_from_settings, settings_from_params
from podcast_ai.infra.config import AppConfig, LLMConfig, Settings, WebSearchConfig


def _base(tmp_path: Path) -> Settings:
    return Settings(
        app=AppConfig(output_dir=str(tmp_path / "out"), music_dir=str(tmp_path / "music")),
        llm=LLMConfig(
            model="base/model",
            base_url="https://openrouter.ai/api/v1",
            web_search=WebSearchConfig(enabled=False, engine="auto", max_results=5, max_uses=None),
        ),
    )


def test_settings_from_params_merges_web_search_three_params(tmp_path: Path) -> None:
    updated = settings_from_params(
        ConsoleParams(
            llm_model="m",
            web_search_enabled=True,
            web_search_engine="firecrawl",
            web_search_max_results=9,
            web_search_max_uses=4,
        ),
        base=_base(tmp_path),
    )
    ws = updated.llm.web_search
    assert ws.enabled is True
    assert ws.engine == "firecrawl"
    assert ws.max_results == 9
    assert ws.max_uses == 4


def test_settings_from_params_max_uses_empty_means_unlimited(tmp_path: Path) -> None:
    for empty in (None, "", 0, -1):
        updated = settings_from_params(
            ConsoleParams(llm_model="m", web_search_max_uses=empty),  # type: ignore[arg-type]
            base=_base(tmp_path),
        )
        assert updated.llm.web_search.max_uses is None


def test_parse_plan_progress_extracts_web_search_requests() -> None:
    logs = (
        "INFO PlanOrchestrator iteration=1 , start_agent=Planner\n"
        "INFO LLM usage server_tool_use.web_search_requests=3\n"
        "INFO PlanOrchestrator round: running agent=Music Curator\n"
        "INFO LLM usage server_tool_use.web_search_requests=7\n"
    )
    progress = parse_plan_progress_from_logs(logs)
    assert progress.web_search_requests == 7
    assert any(e.get("event") == "web_search_usage" for e in progress.events)


def test_parse_plan_progress_no_usage_line_is_none() -> None:
    progress = parse_plan_progress_from_logs("INFO PlanOrchestrator iteration=1 , start_agent=Planner\n")
    assert progress.web_search_requests is None


def test_preset_roundtrip_keeps_web_search_and_debug_keys(tmp_path: Path) -> None:
    values = ConsoleParams(
        topic="v71",
        web_search_enabled=True,
        web_search_engine="exa",
        web_search_max_results=6,
        web_search_max_uses=2,
        debug=True,
    ).as_form_dict()
    for key in (
        "web_search_enabled",
        "web_search_engine",
        "web_search_max_results",
        "web_search_max_uses",
        "debug",
    ):
        assert key in PRESET_KEYS
        assert key in values

    path = save_preset(tmp_path, "v71_ws", values)
    loaded = load_preset(path)
    assert loaded["web_search_enabled"] is True
    assert loaded["web_search_engine"] == "exa"
    assert loaded["web_search_max_results"] == 6
    assert loaded["web_search_max_uses"] == 2
    assert loaded["debug"] is True
    # build_snapshot 不得丢键
    snap = build_snapshot(values)
    assert snap["web_search_engine"] == "exa"


def test_defaults_from_settings_expose_web_search(tmp_path: Path) -> None:
    base = _base(tmp_path)
    base = base.model_copy(
        update={
            "llm": base.llm.model_copy(
                update={
                    "web_search": WebSearchConfig(
                        enabled=True, engine="native", max_results=8, max_uses=1
                    )
                }
            )
        }
    )
    defaults = defaults_from_settings(base)
    assert defaults.web_search_enabled is True
    assert defaults.web_search_engine == "native"
    assert defaults.web_search_max_results == 8
    assert defaults.web_search_max_uses == 1

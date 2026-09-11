"""v5.0 Developer Console：快照、参数映射、启动探测（不启动真实 pipeline）。"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from podcast_ai.console.presets import (
    PRESET_SCHEMA,
    build_snapshot,
    list_preset_names,
    load_preset,
    load_preset_by_name,
    save_preset,
)
from podcast_ai.console.runner import (
    ConsoleParams,
    defaults_from_settings,
    run_plan,
    run_stage2,
    settings_from_params,
)
from podcast_ai.infra.config import AppConfig, AudioConfig, LLMConfig, Settings


def _base_settings(tmp_path: Path) -> Settings:
    return Settings(
        app=AppConfig(output_dir=str(tmp_path / "out"), music_dir=str(tmp_path / "music")),
        llm=LLMConfig(
            model="old/model",
            openrouter_provider="",
            base_url="https://example.invalid/v1",
        ),
        audio=AudioConfig(
            crossfade_seconds=8.0,
            voice_music_crossfade_seconds=3.0,
            voice_music_intro_align_enabled=True,
            voice_music_intro_align_max_seconds=3.0,
        ),
    )


def test_build_snapshot_drops_secrets_and_unknown_keys() -> None:
    payload = build_snapshot(
        {
            "topic": "chill",
            "api_key": "should-not-appear",
            "llm_api_key": "nope",
            "unknown_field": 1,
            "duration_minutes": 45,
        }
    )
    assert payload["schema"] == PRESET_SCHEMA
    assert payload["topic"] == "chill"
    assert payload["duration_minutes"] == 45
    assert "api_key" not in payload
    assert "llm_api_key" not in payload
    assert "unknown_field" not in payload


def test_save_and_load_preset_roundtrip(tmp_path: Path) -> None:
    values = ConsoleParams(topic="Jazz Night", duration_minutes=40, language="en").as_form_dict()
    path = save_preset(tmp_path, "jazz_night", values)
    assert path.name == "jazz_night.json"
    loaded = load_preset(path)
    assert loaded["topic"] == "Jazz Night"
    assert loaded["duration_minutes"] == 40
    assert loaded["language"] == "en"
    assert list_preset_names(tmp_path) == ["jazz_night"]
    assert load_preset_by_name(tmp_path, "jazz_night")["topic"] == "Jazz Night"


def test_load_preset_rejects_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "console_presets" / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="合法 JSON"):
        load_preset(bad)


def test_save_preset_rejects_unsafe_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="非法"):
        save_preset(tmp_path, "../escape", {"topic": "x"})


def test_settings_from_params_overrides_llm_and_audio(tmp_path: Path) -> None:
    base = _base_settings(tmp_path)
    params = ConsoleParams(
        topic="t",
        output_dir=str(tmp_path / "custom_out"),
        llm_model="new/model",
        openrouter_provider="anthropic",
        llm_base_url="https://openrouter.ai/api/v1",
        crossfade_seconds=6.5,
        intro_align_enabled=False,
        intro_align_max_seconds=12.0,
    )
    updated = settings_from_params(params, base=base)
    assert updated.llm.model == "new/model"
    assert updated.llm.openrouter_provider == "anthropic"
    assert updated.llm.base_url == "https://openrouter.ai/api/v1"
    assert updated.app.output_dir == str(tmp_path / "custom_out")
    assert updated.audio.crossfade_seconds == 6.5
    assert updated.audio.voice_music_intro_align_enabled is False
    assert updated.audio.voice_music_intro_align_max_seconds == 12.0
    assert updated.llm.api_key == base.llm.api_key


def test_defaults_from_settings_maps_core_fields(tmp_path: Path) -> None:
    base = _base_settings(tmp_path)
    defaults = defaults_from_settings(base)
    assert defaults.llm_model == "old/model"
    assert defaults.output_dir == str(tmp_path / "out")
    assert defaults.tts_provider == "default"


def test_run_plan_empty_topic_does_not_call_llm(tmp_path: Path) -> None:
    result = run_plan(ConsoleParams(topic="  "), settings=_base_settings(tmp_path))
    assert result.status == "error"
    assert "主题" in result.error


def test_run_stage2_requires_snapshot_path(tmp_path: Path) -> None:
    result = run_stage2(ConsoleParams(topic="x", snapshot_path=""), settings=_base_settings(tmp_path))
    assert result.status == "error"
    assert "snapshot" in result.error.lower()


def test_port_listening_probe() -> None:
    from podcast_ai.console.app import _is_port_listening

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = int(sock.getsockname()[1])
        assert _is_port_listening("127.0.0.1", port)
    assert _is_port_listening("127.0.0.1", port) is False


def _tab_labels(demo) -> list[str]:
    labels: list[str] = []
    for block in getattr(demo, "blocks", {}).values():
        if type(block).__name__ not in {"Tab", "TabItem"}:
            continue
        label = getattr(block, "label", None)
        if label:
            labels.append(str(label))
    return labels


def test_build_app_smoke() -> None:
    pytest.importorskip("gradio")
    from podcast_ai.console.app import build_app

    demo = build_app()
    assert demo is not None
    labels = _tab_labels(demo)
    assert any("阶段一" in x for x in labels)
    assert any("阶段二" in x for x in labels)
    assert any("阶段三" in x for x in labels)
    assert labels[0].startswith("阶段一")


def test_insight_md_shows_failure_and_single_agent() -> None:
    from podcast_ai.console.app import _insight_md
    from podcast_ai.console.runner import ConsoleRunResult

    failed = ConsoleRunResult(
        status="error",
        command="plan",
        error="LLM timeout",
        plan_iteration=2,
        plan_current_agent="Critic",
    )
    md = _insight_md(failed)
    assert "2" in md and "Critic" in md and "LLM timeout" in md

    staged = ConsoleRunResult(
        status="running",
        command="plan",
        orchestration_mode="staged",
        plan_stage="planner",
        plan_revision=1,
        plan_current_agent="Critic",
    )
    md_st = _insight_md(staged, agent_mode="multi_agent")
    assert "staged" in md_st and "planner" in md_st and "`1`" in md_st

    single = ConsoleRunResult(status="success", command="plan", plan_current_agent="single_agent")
    md_sa = _insight_md(single, agent_mode="single_agent", orchestration_mode="legacy")
    assert "N/A" in md_sa and "single_agent" in md_sa
    assert "legacy" in md_sa and "不应用" in md_sa


def test_cli_commands_not_removed() -> None:
    """v5.0 只新增 console，既有子命令必须仍在。"""
    from podcast_ai.cli import app

    names: set[str] = set()
    for cmd in app.registered_commands:
        if cmd.name:
            names.add(cmd.name)
        elif cmd.callback is not None:
            names.add(cmd.callback.__name__.replace("_", "-"))
    expected = {
        "version",
        "plan-episode",
        "init-config",
        "create-episode",
        "create-episode-stage2",
        "finalize-episode-stage3",
        "scan-library",
        "console",
    }
    assert expected <= names

"""把 Console 表单映射为 Settings / pipeline 调用；捕获错误，不复制业务逻辑。"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import ValidationError

from podcast_ai.console.run_progress import (
    merge_progress,
    parse_plan_progress_from_logs,
    progress_from_events,
)
from podcast_ai.core.exceptions import PodcastAIError
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.core.pipeline import (
    create_episode,
    create_episode_stage2,
    finalize_episode_stage3,
    plan_episode,
)
from podcast_ai.infra.config import Settings, load_settings

logger = logging.getLogger(__name__)

CommandName = Literal["plan", "stage2", "stage3", "create"]
RunStatus = Literal["idle", "running", "success", "error"]

_TTS_PROVIDERS = frozenset({"edge", "elevenlabs", "minimax"})
_LANGUAGES = frozenset({"zh", "en"})
_AGENT_MODES = frozenset({"single_agent", "multi_agent"})


@dataclass
class ConsoleParams:
    """与 Gradio 表单字段一一对应（不含密钥）。"""

    topic: str = ""
    duration_minutes: int = 60
    language: str = "zh"
    agent_mode: str = "multi_agent"
    output_dir: str = "./output"
    llm_model: str = ""
    openrouter_provider: str = ""
    llm_base_url: str = ""
    snapshot_path: str = ""
    music_dir: str = "./music"
    tts_provider: str = "default"
    mix_params_path: str = ""
    crossfade_seconds: float = 8.0
    voice_music_crossfade_seconds: float = 3.0
    intro_align_enabled: bool = True
    intro_align_max_seconds: float = 3.0

    def as_form_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConsoleRunResult:
    status: RunStatus
    command: CommandName | str
    elapsed_ms: int = 0
    error: str = ""
    logs: str = ""
    summary: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    audio_path: str | None = None
    show_notes: str = ""
    snapshot_path: str | None = None
    mix_params_path: str | None = None
    # v5.2：阶段一运行态洞察（供 Console UI 读取；其他命令保持默认空）
    plan_iteration: int | None = None
    plan_current_agent: str | None = None
    plan_progress_events: list[dict[str, Any]] = field(default_factory=list)
    # v6.0：编排可观测（config 只读；staged 运行时再填 stage/revision）
    orchestration_mode: str | None = None
    plan_stage: str | None = None
    plan_revision: int | None = None

    @classmethod
    def running(cls, command: CommandName) -> ConsoleRunResult:
        labels = {
            "plan": "plan-episode",
            "stage2": "create-episode-stage2",
            "stage3": "finalize-episode-stage3",
            "create": "create-episode",
        }
        return cls(
            status="running",
            command=command,
            summary=f"正在运行 `{labels[command]}` …",
        )


def defaults_from_settings(settings: Settings | None = None) -> ConsoleParams:
    s = settings or load_settings()
    return ConsoleParams(
        output_dir=s.app.output_dir,
        music_dir=s.app.music_dir,
        llm_model=s.llm.model,
        openrouter_provider=s.llm.openrouter_provider or "",
        llm_base_url=s.llm.base_url,
        tts_provider="default",
        crossfade_seconds=s.audio.crossfade_seconds,
        voice_music_crossfade_seconds=s.audio.voice_music_crossfade_seconds,
        intro_align_enabled=s.audio.voice_music_intro_align_enabled,
        intro_align_max_seconds=s.audio.voice_music_intro_align_max_seconds,
    )


def settings_from_params(params: ConsoleParams, base: Settings | None = None) -> Settings:
    """用表单覆盖 Settings 的常用字段；密钥仍来自 .env / config。"""
    settings = base or load_settings()
    llm_update: dict[str, Any] = {
        "openrouter_provider": (params.openrouter_provider or "").strip(),
    }
    if (params.llm_model or "").strip():
        llm_update["model"] = params.llm_model.strip()
    if (params.llm_base_url or "").strip():
        llm_update["base_url"] = params.llm_base_url.strip()

    app_update: dict[str, Any] = {}
    if (params.output_dir or "").strip():
        app_update["output_dir"] = params.output_dir.strip()
    if (params.music_dir or "").strip():
        app_update["music_dir"] = params.music_dir.strip()

    return settings.model_copy(
        deep=True,
        update={
            "llm": settings.llm.model_copy(update=llm_update),
            "app": settings.app.model_copy(update=app_update),
            "audio": settings.audio.model_copy(
                update={
                    "crossfade_seconds": float(params.crossfade_seconds),
                    "voice_music_crossfade_seconds": float(params.voice_music_crossfade_seconds),
                    "voice_music_intro_align_enabled": bool(params.intro_align_enabled),
                    "voice_music_intro_align_max_seconds": float(params.intro_align_max_seconds),
                }
            ),
        },
    )


def _tts_override(params: ConsoleParams) -> Literal["edge", "elevenlabs", "minimax"] | None:
    raw = (params.tts_provider or "").strip().lower()
    if raw in ("", "default"):
        return None
    if raw not in _TTS_PROVIDERS:
        raise PodcastAIError("tts_provider 仅支持 default / edge / elevenlabs / minimax")
    return raw  # type: ignore[return-value]


def _language(params: ConsoleParams) -> Literal["zh", "en"]:
    raw = (params.language or "zh").strip().lower()
    if raw not in _LANGUAGES:
        raise PodcastAIError("language 仅支持 zh / en")
    return raw  # type: ignore[return-value]


def _agent_mode(params: ConsoleParams) -> Literal["single_agent", "multi_agent"]:
    raw = (params.agent_mode or "multi_agent").strip()
    if raw not in _AGENT_MODES:
        raise PodcastAIError("agent_mode 仅支持 single_agent / multi_agent")
    return raw  # type: ignore[return-value]


class _BufferLogHandler(logging.Handler):
    """把本次 Run 的 logging 抓到内存，供 UI 展示；不改 pipeline。"""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


def _format_elapsed(elapsed_ms: int) -> str:
    if elapsed_ms < 1000:
        return f"{elapsed_ms}ms"
    return f"{elapsed_ms / 1000:.1f}s"


def _run_command(command: CommandName, fn: Callable[[], ConsoleRunResult]) -> ConsoleRunResult:
    handler = _BufferLogHandler()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    started = time.perf_counter()
    try:
        result = fn()
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        result.logs = "\n".join(handler.lines)
        if result.status == "success" and result.summary:
            result.summary = f"{result.summary} · {_format_elapsed(result.elapsed_ms)}"
        return result
    except PodcastAIError as exc:
        return ConsoleRunResult(
            status="error",
            command=command,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
            logs="\n".join(handler.lines),
            summary=f"失败 · {_format_elapsed(int((time.perf_counter() - started) * 1000))}",
        )
    except ValidationError as exc:
        return ConsoleRunResult(
            status="error",
            command=command,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"参数校验失败：{exc}",
            logs="\n".join(handler.lines),
            summary="失败（校验）",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Console 调用 pipeline 时发生未预期错误")
        return ConsoleRunResult(
            status="error",
            command=command,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"未预期错误：{exc}",
            logs="\n".join(handler.lines),
            summary="失败",
        )
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


def run_plan(
    params: ConsoleParams,
    *,
    settings: Settings | None = None,
    progress_sink: list[dict[str, Any]] | None = None,
) -> ConsoleRunResult:
    # 可注入外部 list，供 UI 在运行中轮询 iteration/agent（默认仍用内部 list）。
    progress_events: list[dict[str, Any]] = progress_sink if progress_sink is not None else []

    def _on_progress(ev: dict[str, Any]) -> None:
        progress_events.append(dict(ev))

    def _attach_progress(result: ConsoleRunResult) -> ConsoleRunResult:
        from_hooks = progress_from_events(
            progress_events,
            failure_reason=result.error or None,
        )
        from_logs = parse_plan_progress_from_logs(result.logs)
        merged = merge_progress(from_hooks, from_logs)
        # single_agent：无 multi 日志时标注 current_agent
        if merged.current_agent is None and _agent_mode(params) == "single_agent":
            merged.current_agent = "single_agent"
        if result.error and not merged.failure_reason:
            merged.failure_reason = result.error
        result.plan_iteration = merged.iteration
        result.plan_current_agent = merged.current_agent
        result.plan_progress_events = list(merged.events)
        result.plan_stage = merged.stage
        result.plan_revision = merged.revision
        if result.orchestration_mode is None:
            result.orchestration_mode = str(
                settings_from_params(params, base=settings).app.orchestration_mode
            )
        if merged.failure_reason and not result.error:
            result.error = merged.failure_reason
        return result

    def _inner() -> ConsoleRunResult:
        if not (params.topic or "").strip():
            raise PodcastAIError("主题不能为空。")
        effective = settings_from_params(params, base=settings)
        output_dir = Path(params.output_dir.strip() or effective.app.output_dir)
        request = EpisodeRequest(
            topic=params.topic.strip(),
            duration_minutes=int(params.duration_minutes),
            language=_language(params),
            output_dir=output_dir,
        )
        plan, state_path, snapshot_path = plan_episode(
            request,
            settings=effective,
            agent_mode=_agent_mode(params),
            on_progress=_on_progress,
        )
        orch = str(effective.app.orchestration_mode)
        artifacts = {
            "state.json": str(state_path),
            "snapshot": str(snapshot_path),
        }
        return ConsoleRunResult(
            status="success",
            command="plan",
            summary=(
                f"阶段一完成 · Episode `{snapshot_path.stem}` · "
                f"plan_id `{plan.plan_id}` · agent_mode `{_agent_mode(params)}` · "
                f"orchestration_mode `{orch}`"
            ),
            artifacts=artifacts,
            snapshot_path=str(snapshot_path),
            orchestration_mode=orch,
        )

    result = _run_command("plan", _inner)
    return _attach_progress(result)


def run_stage2(params: ConsoleParams, *, settings: Settings | None = None) -> ConsoleRunResult:
    def _inner() -> ConsoleRunResult:
        snapshot_raw = (params.snapshot_path or "").strip()
        music_raw = (params.music_dir or "").strip()
        if not snapshot_raw:
            raise PodcastAIError("请填写阶段一 snapshot 路径（`<episode_id>.json`）。")
        if not music_raw:
            raise PodcastAIError("请填写音乐目录。")
        snapshot = Path(snapshot_raw)
        music_dir = Path(music_raw)
        mix_params_path = create_episode_stage2(
            snapshot_path=snapshot,
            music_dir=music_dir,
            settings=settings_from_params(params, base=settings),
            topic=(params.topic or "").strip() or None,
            language=_language(params),
            tts_provider=_tts_override(params),
        )
        return ConsoleRunResult(
            status="success",
            command="stage2",
            summary=f"阶段二完成 · MixParamsJSON `{mix_params_path.name}`",
            artifacts={"mix_params": str(mix_params_path)},
            mix_params_path=str(mix_params_path),
        )

    return _run_command("stage2", _inner)


def run_stage3(params: ConsoleParams, *, settings: Settings | None = None) -> ConsoleRunResult:
    def _inner() -> ConsoleRunResult:
        mix_raw = (params.mix_params_path or "").strip()
        if not mix_raw:
            raise PodcastAIError("请填写 MixParamsJSON 路径（`*_mix_params.json`）。")
        mix_path = Path(mix_raw)
        result = finalize_episode_stage3(
            mix_params_json_path=mix_path,
            settings=settings_from_params(params, base=settings),
            topic=(params.topic or "").strip() or None,
        )
        return _result_from_episode("stage3", result)

    return _run_command("stage3", _inner)


def run_create(params: ConsoleParams, *, settings: Settings | None = None) -> ConsoleRunResult:
    def _inner() -> ConsoleRunResult:
        snapshot_raw = (params.snapshot_path or "").strip()
        music_raw = (params.music_dir or "").strip()
        if not snapshot_raw:
            raise PodcastAIError("请填写阶段一 snapshot 路径（`<episode_id>.json`）。")
        if not music_raw:
            raise PodcastAIError("请填写音乐目录。")
        snapshot = Path(snapshot_raw)
        music_dir = Path(music_raw)
        result = create_episode(
            snapshot_path=snapshot,
            music_dir=music_dir,
            settings=settings_from_params(params, base=settings),
            topic=(params.topic or "").strip() or None,
            language=_language(params),
            tts_provider=_tts_override(params),
        )
        return _result_from_episode("create", result)

    return _run_command("create", _inner)


def _result_from_episode(command: CommandName, result: Any) -> ConsoleRunResult:
    audio = Path(result.audio_path)
    episode_root = audio.parent.parent
    notes_path = episode_root / "final" / f"{result.episode_id}_show_notes.md"
    mix_wav = episode_root / "mix" / "mix.wav"
    artifacts: dict[str, str] = {"final_mp3": str(audio)}
    if notes_path.is_file():
        artifacts["show_notes"] = str(notes_path)
    if mix_wav.is_file():
        artifacts["mix.wav"] = str(mix_wav)
    show_notes = result.show_notes or ""
    if not show_notes and notes_path.is_file():
        show_notes = notes_path.read_text(encoding="utf-8")
    audio_str = str(audio) if audio.is_file() else None
    return ConsoleRunResult(
        status="success",
        command=command,
        summary=(
            f"导出完成 · `{result.episode_id}` · "
            f"{result.actual_duration_seconds}s · {len(result.tracks)} 首"
        ),
        artifacts=artifacts,
        audio_path=audio_str,
        show_notes=show_notes,
    )

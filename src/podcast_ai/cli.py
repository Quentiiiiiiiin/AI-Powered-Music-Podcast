from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from podcast_ai.core.exceptions import PodcastAIError
from podcast_ai.core.logging_config import configure_logging
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.core.pipeline import (
    create_episode as pipeline_create_episode,
    create_episode_stage2 as pipeline_create_episode_stage2,
    finalize_episode_stage3 as pipeline_finalize_episode_stage3,
)
from podcast_ai.core.pipeline import plan_episode as pipeline_plan_episode
from podcast_ai.infra.config import load_settings


app = typer.Typer(
    add_completion=False,
    help="podcast-ai：AI 音乐 Podcast 自动生成工具（MVP）。",
)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    show_version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="打印版本号并退出。",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        envvar="PODCAST_AI_LOG_LEVEL",
        help="日志等级（DEBUG/INFO/WARNING/ERROR）。",
        show_default=True,
    ),
    log_file: str = typer.Option(
        "",
        "--log-file",
        envvar="PODCAST_AI_LOG_FILE",
        help="可选：日志文件路径（追加写入）。",
        show_default=False,
    ),
) -> None:
    """命令行入口（后续任务会逐步增加子命令）。"""
    configure_logging(level=log_level, log_file=(log_file or None))
    if show_version:
        from podcast_ai import __version__

        typer.echo(__version__)
        raise typer.Exit(code=0)

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


@app.command()
def version() -> None:
    """打印当前版本号。"""
    from podcast_ai import __version__

    typer.echo(__version__)


@app.command("plan-episode")
def plan_episode_cli(
    topic: str = typer.Argument(..., help="节目主题（如：Late Night Chill Electronic）"),
    duration_minutes: int = typer.Argument(..., help="目标时长（分钟）"),
    agent_mode: Literal["single_agent", "multi_agent"] = typer.Option(
        "multi_agent",
        "--agent-mode",
        help="阶段一生成模式：single_agent 或 multi_agent。",
        show_default=True,
    ),
    language: str = typer.Option(
        "zh",
        "--language",
        "-l",
        help="串词与规划语言（zh/en）。",
        show_default=True,
    ),
    output_dir: Path = typer.Option(
        None,  # type: ignore[arg-type]
        "--output-dir",
        "-o",
        help="输出目录（覆盖配置中的 app.output_dir）。",
    ),
) -> None:
    """
    阶段一：根据主题与时长生成规划结果；state 统一落盘为 state.json。
    single_agent 输出为 PlanState 子集（不含 critic/control）。
    """
    settings = load_settings()
    effective_output_dir = Path(output_dir) if output_dir is not None else Path(settings.app.output_dir)

    request = EpisodeRequest(
        topic=topic,
        duration_minutes=duration_minutes,
        language=language,
        output_dir=effective_output_dir,
    )

    try:
        plan, state_path, snapshot_path = pipeline_plan_episode(
            request,
            settings=settings,
            agent_mode=agent_mode,
        )
    except PodcastAIError as exc:
        typer.echo(f"[错误] 规划失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("规划完成：")
    typer.echo(f"- Episode ID: {snapshot_path.stem}")
    typer.echo(f"- Plan ID（内存标识，未单独落盘）：{plan.plan_id}")
    typer.echo(f"- state.json（统一状态）：{state_path}")
    typer.echo(f"- {snapshot_path.name}（阶段一对接子集文件，v3.8）：{snapshot_path}")


_INIT_CONFIG_YAML = """app:
  music_dir: "./music"
  output_dir: "./output"

audio:
  crossfade_seconds: 8.0
  voice_music_crossfade_seconds: 3.0
  voice_music_intro_align_enabled: true
  voice_music_intro_align_max_seconds: 3.0
  loudness_target_lufs: -14.0
  per_track_normalize_enabled: true

cache:
  enabled: true
  dir: "./output/cache"

llm:
  provider: "openai_compatible"
  api_key: ""
  base_url: "https://openrouter.ai/api/v1"
  model: "openai/gpt-4o-mini"
  timeout_seconds: 60
  max_retries: 2
  # v4.6：OpenRouter 供应方路由。留空=请求体不携带 provider（由 OpenRouter 自动选路）。
  # 非空：填单个 slug（实现为 {"only":[slug]}）或 JSON 对象字符串（与官方 provider 字段一致）。
  openrouter_provider: "azure/swedencentral"

tts:
  provider: "elevenlabs"
  api_key: ""
  voice: ""
  # 通用超时/重试配置
  timeout_seconds: 60
  max_retries: 2
  # v1.4+：当 provider 为 elevenlabs 时必填 api_key、voice_id
  elevenlabs:
    api_key: ""
    voice_id: ""
    model: "eleven_multilingual_v2"
    output_format: "mp3_44100_128"
  # v4.1：MiniMax（同步非流式）
  minimax:
    api_key: ""
    model: "speech-2.8-hd"
    voice_id: "male-qn-qingse"
    query_interval_ms: 500
    query_timeout_seconds: 30
"""

_INIT_ENV_EXAMPLE = """# LLM / TTS key 建议放环境变量
PODCAST_AI_LLM__API_KEY=your_llm_api_key_here
# PODCAST_AI_LLM__BASE_URL=https://openrouter.ai/api/v1
# v4.6：OpenRouter 供应方路由（仅当 base_url 为 OpenRouter 时生效；留空=自动选路）
# PODCAST_AI_LLM__OPENROUTER_PROVIDER=
PODCAST_AI_TTS__PROVIDER=elevenlabs
PODCAST_AI_TTS__ELEVENLABS__API_KEY=your_elevenlabs_api_key_here
PODCAST_AI_TTS__ELEVENLABS__VOICE_ID=your_elevenlabs_voice_id_here
# PODCAST_AI_TTS__PROVIDER=minimax
# PODCAST_AI_TTS__MINIMAX__API_KEY=your_minimax_api_key_here
# PODCAST_AI_TTS__MINIMAX__MODEL=speech-2.8-hd
# PODCAST_AI_TTS__MINIMAX__VOICE_ID=male-qn-qingse
"""


@app.command("init-config")
def init_config_cli(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="覆盖已存在的配置文件。",
    ),
    target_dir: Path = typer.Option(
        Path("."),
        "--dir",
        "-d",
        path_type=Path,
        help="目标目录。",
    ),
) -> None:
    """
    生成默认 config.yaml 与 .env.example，可复制后重命名为 .env 并填入 API Key。
    """
    config_path = target_dir / "config.yaml"
    env_path = target_dir / ".env.example"
    if not force and config_path.exists():
        typer.echo(f"[跳过] {config_path} 已存在，使用 --force 覆盖。", err=True)
        raise typer.Exit(code=1)
    if not force and env_path.exists():
        typer.echo(f"[跳过] {env_path} 已存在，使用 --force 覆盖。", err=True)
        raise typer.Exit(code=1)
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_INIT_CONFIG_YAML, encoding="utf-8")
    env_path.write_text(_INIT_ENV_EXAMPLE, encoding="utf-8")
    typer.echo(f"已生成：{config_path.resolve()}")
    typer.echo(f"已生成：{env_path.resolve()}")
    typer.echo("请复制 .env.example 为 .env 并填入 LLM/TTS API Key。")


@app.command("create-episode")
def create_episode_cli(
    plan_file: Path = typer.Argument(
        ...,
        path_type=Path,
        help="阶段一输出的 snapshot 路径（<episode_id>.json）。",
    ),
    music_dir: Path = typer.Argument(
        ...,
        path_type=Path,
        help="本期节目音乐目录（已按目标歌单准备好歌曲）。",
    ),
    topic: str = typer.Option(
        "",
        "--topic",
        "-t",
        help="节目主题（用于 Show Notes 标题，可选）。",
    ),
    language: str = typer.Option(
        "zh",
        "--language",
        "-l",
        help="串词与 TTS 语言。",
    ),
    tts_provider: Literal["edge", "elevenlabs", "minimax"] | None = typer.Option(
        None,
        "--tts-provider",
        help="覆盖配置中的 TTS 供应商（edge/elevenlabs/minimax）。",
    ),
) -> None:
    """
    阶段二：从规划文件继续，扫描音乐库 → 选曲 → 主持 TTS → 混音 → 母带 → 导出。
    """
    if not plan_file.exists():
        typer.echo(f"[错误] 规划文件不存在：{plan_file}", err=True)
        raise typer.Exit(code=1)
    if not music_dir.exists() or not music_dir.is_dir():
        typer.echo(f"[错误] 音乐目录不存在或非目录：{music_dir}", err=True)
        raise typer.Exit(code=1)
    try:
        result = pipeline_create_episode(
            snapshot_path=plan_file,
            music_dir=music_dir,
            topic=topic or None,
            language=language,
            tts_provider=tts_provider,
        )
    except PodcastAIError as exc:
        typer.echo(f"[错误] 制作失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    episode_root = result.audio_path.parent.parent
    show_notes_path = episode_root / "final" / f"{result.episode_id}_show_notes.md"
    typer.echo("制作完成：")
    typer.echo(f"- 音频：{result.audio_path}")
    typer.echo(f"- Show Notes：{show_notes_path}")
    typer.echo(f"- 时长：{result.actual_duration_seconds}s，曲目：{len(result.tracks)} 首")


@app.command("create-episode-stage2")
def create_episode_stage2_cli(
    snapshot_path: Path = typer.Argument(
        ...,
        path_type=Path,
        help="阶段一输出的 snapshot 路径（<episode_id>.json）。",
    ),
    music_dir: Path = typer.Argument(
        ...,
        path_type=Path,
        help="本期节目音乐目录（已按目标歌单准备好歌曲）。",
    ),
    topic: str = typer.Option(
        "",
        "--topic",
        "-t",
        help="节目主题（用于 Show Notes 标题，可选）。",
    ),
    language: str = typer.Option(
        "zh",
        "--language",
        "-l",
        help="串词与 TTS 语言。",
    ),
    tts_provider: Literal["edge", "elevenlabs", "minimax"] | None = typer.Option(
        None,
        "--tts-provider",
        help="覆盖配置中的 TTS 供应商（edge/elevenlabs/minimax）。",
    ),
) -> None:
    """阶段二：生成可编辑混音参数 JSON（不导出最终音频）。"""
    if not snapshot_path.exists():
        typer.echo(f"[错误] snapshot 文件不存在：{snapshot_path}", err=True)
        raise typer.Exit(code=1)
    if not music_dir.exists() or not music_dir.is_dir():
        typer.echo(f"[错误] 音乐目录不存在或非目录：{music_dir}", err=True)
        raise typer.Exit(code=1)

    try:
        mix_params_json_path = pipeline_create_episode_stage2(
            snapshot_path=snapshot_path,
            music_dir=music_dir,
            topic=topic or None,
            language=language,
            tts_provider=tts_provider,
        )
    except PodcastAIError as exc:
        typer.echo(f"[错误] 阶段二失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("阶段二完成：")
    typer.echo(f"- MixParamsJSON：{mix_params_json_path}")


@app.command("finalize-episode-stage3")
def finalize_episode_stage3_cli(
    mix_params_json_path: Path = typer.Argument(
        ...,
        path_type=Path,
        help="阶段二生成的 MixParamsJSON 路径（*_mix_params.json）。",
    ),
    topic: str = typer.Option(
        "",
        "--topic",
        "-t",
        help="节目主题（覆盖 MixParamsJSON 中 topic，可选）。",
    ),
) -> None:
    """阶段三：读取并校验 MixParamsJSON，完成最终混音导出。"""
    if not mix_params_json_path.exists() or not mix_params_json_path.is_file():
        typer.echo(f"[错误] MixParamsJSON 不存在或非文件：{mix_params_json_path}", err=True)
        raise typer.Exit(code=1)

    try:
        result = pipeline_finalize_episode_stage3(
            mix_params_json_path=mix_params_json_path,
            topic=topic or None,
        )
    except PodcastAIError as exc:
        typer.echo(f"[错误] 阶段三失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    episode_root = result.audio_path.parent.parent
    show_notes_path = episode_root / "final" / f"{result.episode_id}_show_notes.md"

    typer.echo("阶段三完成：")
    typer.echo(f"- 音频：{result.audio_path}")
    typer.echo(f"- Show Notes：{show_notes_path}")
    typer.echo(f"- 时长：{result.actual_duration_seconds}s，曲目：{len(result.tracks)} 首")


@app.command("scan-library")
def scan_library_cli(
    music_dir: Path = typer.Argument(
        ...,
        path_type=Path,
        help="要扫描的音乐目录（mp3/wav）。",
    ),
    no_bpm: bool = typer.Option(
        False,
        "--no-bpm",
        help="跳过 BPM 分析以提速。",
    ),
) -> None:
    """
    扫描音乐库并缓存元数据，可用于提前了解曲目或验证目录。
    """
    if not music_dir.exists() or not music_dir.is_dir():
        typer.echo(f"[错误] 目录不存在或非目录：{music_dir}", err=True)
        raise typer.Exit(code=1)
    from podcast_ai.modules.library.scanner import LibraryScanner

    scanner = LibraryScanner(bpm_max_seconds=None if no_bpm else 30.0)
    try:
        tracks = scanner.scan_or_load_cache(music_dir)
    except PodcastAIError as exc:
        typer.echo(f"[错误] 扫描失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"扫描完成：{len(tracks)} 首")
    for t in tracks[:10]:
        title = t.track.title or t.track.file_path.name
        dur = t.metadata.duration_seconds
        bpm = f", BPM {t.metadata.bpm:.0f}" if t.metadata.bpm else ""
        typer.echo(f"  - {title} ({dur:.0f}s{bpm})")
    if len(tracks) > 10:
        typer.echo(f"  ... 及另外 {len(tracks) - 10} 首")


@app.command("console")
def console_cli(
    port: int = typer.Option(
        7860,
        "--port",
        "-p",
        help="HTTP 端口。",
        show_default=True,
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="绑定地址（默认仅本机）。",
        show_default=True,
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="不自动打开浏览器。",
    ),
) -> None:
    """
    启动本地 Developer Console（Gradio）。仅供开发调试，非正式产品 UI。
    默认 http://127.0.0.1:7860
    """
    try:
        from podcast_ai.console.app import ConsoleLaunchError, launch_console
    except ImportError as exc:
        typer.echo(
            "[错误] 无法导入 Developer Console（可能未安装 gradio）。\n"
            "请执行：pip install -e .\n"
            f"详情：{exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    try:
        launch_console(host=host, port=port, inbrowser=not no_browser)
    except ConsoleLaunchError as exc:
        typer.echo(f"[错误] {exc}", err=True)
        raise typer.Exit(code=1) from exc


def main() -> None:
    app()


if __name__ == "__main__":
    main()


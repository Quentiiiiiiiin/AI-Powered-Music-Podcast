from __future__ import annotations

from pathlib import Path

import typer

from podcast_ai.core.exceptions import PodcastAIError
from podcast_ai.core.logging_config import configure_logging
from podcast_ai.core.models import EpisodeRequest
from podcast_ai.core.pipeline import create_episode as pipeline_create_episode
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
    阶段一：根据主题与时长生成 EpisodePlan 与目标歌单规划。
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
        plan, plan_path, playlist_path = pipeline_plan_episode(request, settings=settings)
    except PodcastAIError as exc:
        typer.echo(f"[错误] 规划失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("规划完成：")
    typer.echo(f"- Episode ID: {plan.plan_id}")
    typer.echo(f"- 规划文件（JSON）：{plan_path}")
    typer.echo(f"- 目标歌单（Markdown）：{playlist_path}")


_INIT_CONFIG_YAML = """app:
  music_dir: "./music"
  output_dir: "./output"

audio:
  crossfade_seconds: 8.0
  loudness_target_lufs: -14.0

cache:
  enabled: true
  dir: "./output/cache"

llm:
  provider: "openai_compatible"
  api_key: ""
  base_url: "https://openrouter.ai/api/v1"
  model: "minimax/minimax-m2.5"
  timeout_seconds: 60
  max_retries: 2

tts:
  provider: "edge_tts"
  api_key: ""
  voice: "en-AU-NatashaNeural"
  timeout_seconds: 60
  max_retries: 2
"""

_INIT_ENV_EXAMPLE = """# LLM / TTS key 建议放环境变量
PODCAST_AI_LLM__API_KEY=your_llm_api_key_here
# PODCAST_AI_LLM__BASE_URL=https://openrouter.ai/api/v1
# PODCAST_AI_TTS__VOICE=your_voice_here
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
        help="规划文件路径（episode_plan_*.json）。",
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
            plan_path=plan_file,
            music_dir=music_dir,
            topic=topic or None,
            language=language,
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()


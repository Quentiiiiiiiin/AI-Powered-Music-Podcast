"""Gradio Blocks：只装配表单与展示，业务调用 runner → pipeline。"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Iterator

from podcast_ai.console.presets import list_preset_names, load_preset_by_name, save_preset
from podcast_ai.console.runner import (
    ConsoleParams,
    ConsoleRunResult,
    defaults_from_settings,
    run_create,
    run_plan,
    run_stage2,
    run_stage3,
)
from podcast_ai.infra.config import load_settings


class ConsoleLaunchError(Exception):
    """启动失败（缺依赖、端口占用、导入错误等），供 CLI 打印后退出。"""


def _is_port_listening(host: str, port: int) -> bool:
    probe = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((probe, int(port))) == 0


def _params_from_form(
    topic: str,
    duration_minutes: float | int | None,
    language: str,
    agent_mode: str,
    output_dir: str,
    llm_model: str,
    openrouter_provider: str,
    llm_base_url: str,
    snapshot_path: str,
    music_dir: str,
    tts_provider: str,
    mix_params_path: str,
    crossfade_seconds: float | None,
    voice_music_crossfade_seconds: float | None,
    intro_align_enabled: bool,
    intro_align_max_seconds: float | None,
) -> ConsoleParams:
    defaults = defaults_from_settings()
    return ConsoleParams(
        topic=topic or "",
        duration_minutes=int(duration_minutes or defaults.duration_minutes),
        language=language or "zh",
        agent_mode=agent_mode or "multi_agent",
        output_dir=output_dir or defaults.output_dir,
        llm_model=llm_model or "",
        openrouter_provider=openrouter_provider or "",
        llm_base_url=llm_base_url or "",
        snapshot_path=snapshot_path or "",
        music_dir=music_dir or defaults.music_dir,
        tts_provider=tts_provider or "default",
        mix_params_path=mix_params_path or "",
        crossfade_seconds=float(
            crossfade_seconds if crossfade_seconds is not None else defaults.crossfade_seconds
        ),
        voice_music_crossfade_seconds=float(
            voice_music_crossfade_seconds
            if voice_music_crossfade_seconds is not None
            else defaults.voice_music_crossfade_seconds
        ),
        intro_align_enabled=bool(intro_align_enabled),
        intro_align_max_seconds=float(
            intro_align_max_seconds
            if intro_align_max_seconds is not None
            else defaults.intro_align_max_seconds
        ),
    )


def _status_md(result: ConsoleRunResult) -> str:
    badge = {
        "idle": "⚪ idle",
        "running": "🟡 **running**",
        "success": "🟢 **success**",
        "error": "🔴 **error**",
    }.get(result.status, result.status)
    elapsed = ""
    if result.elapsed_ms:
        elapsed = f" · {_fmt_ms(result.elapsed_ms)}"
    involved = {
        "plan": "LLM（Planner / Curator / Writer / Critic）",
        "stage2": "Music 映射 · TTS · Audio Processing（mix_params）",
        "stage3": "Audio Processing · Final Output",
        "create": "Music 映射 · TTS · Audio Processing · Final Output",
    }.get(str(result.command), "")
    involved_line = f"\n涉及：{involved}" if involved else ""
    return f"### Pipeline Status\n{badge} `{result.command}`{elapsed}{involved_line}"


def _fmt_ms(elapsed_ms: int) -> str:
    if elapsed_ms < 1000:
        return f"{elapsed_ms}ms"
    return f"{elapsed_ms / 1000:.1f}s"


def _artifacts_md(result: ConsoleRunResult) -> str:
    if not result.artifacts:
        return "_（尚无产物路径）_"
    lines = ["**产物路径**"]
    for key, path in result.artifacts.items():
        lines.append(f"- `{key}`: `{path}`")
    return "\n".join(lines)


def _audio_value(result: ConsoleRunResult) -> str | None:
    if not result.audio_path:
        return None
    path = Path(result.audio_path)
    return str(path) if path.is_file() else None


def _ui_pack(
    result: ConsoleRunResult,
    snapshot_path: str,
    mix_params_path: str,
) -> tuple[Any, ...]:
    snap = result.snapshot_path or snapshot_path
    mix = result.mix_params_path or mix_params_path
    return (
        _status_md(result),
        result.summary or "",
        _artifacts_md(result),
        result.error or "",
        _audio_value(result),
        result.show_notes or "",
        result.logs or "",
        snap,
        mix,
    )


def _yield_run(
    command: str,
    params: ConsoleParams,
    runner: Any,
) -> Iterator[tuple[Any, ...]]:
    running = ConsoleRunResult.running(command)  # type: ignore[arg-type]
    yield _ui_pack(running, params.snapshot_path, params.mix_params_path)
    result = runner(params)
    yield _ui_pack(result, params.snapshot_path, params.mix_params_path)


def build_app():
    """组装 Gradio Blocks。延迟 import，便于 CLI 在缺依赖时给出明确错误。"""
    import gradio as gr

    defaults = defaults_from_settings()
    preset_choices = list_preset_names(defaults.output_dir)

    with gr.Blocks(title="podcast-ai Developer Console") as demo:
        gr.Markdown(
            "## podcast-ai Developer Console\n"
            "本地开发者调试入口（**非正式产品 UI**）。"
            "顶部切换阶段一 / 二 / 三，下方只显示当前阶段；业务仍走 `core.pipeline`。"
        )

        with gr.Accordion("参数快照（Preset，跨阶段共用）", open=False):
            with gr.Row():
                preset_name = gr.Textbox(label="快照名称", placeholder="例如 debug_rnb_60min", scale=3)
                preset_pick = gr.Dropdown(
                    label="已保存快照",
                    choices=preset_choices,
                    value=None,
                    allow_custom_value=False,
                    scale=3,
                )
            with gr.Row():
                save_btn = gr.Button("保存当前参数")
                load_btn = gr.Button("加载所选快照")
                refresh_btn = gr.Button("刷新列表")
            preset_msg = gr.Markdown("")

        with gr.Tabs():
            with gr.Tab("阶段一 · 计划生成"):
                topic = gr.Textbox(label="topic", placeholder="Late Night Chill Electronic", lines=2)
                with gr.Row():
                    duration_minutes = gr.Number(
                        label="duration_minutes",
                        value=defaults.duration_minutes,
                        minimum=1,
                        precision=0,
                    )
                    language = gr.Radio(choices=["zh", "en"], value="zh", label="language")
                    agent_mode = gr.Radio(
                        choices=["multi_agent", "single_agent"],
                        value="multi_agent",
                        label="agent_mode",
                    )
                output_dir = gr.Textbox(label="output_dir", value=defaults.output_dir)
                llm_model = gr.Textbox(label="model", value=defaults.llm_model)
                openrouter_provider = gr.Textbox(
                    label="openrouter_provider",
                    value=defaults.openrouter_provider,
                    placeholder="留空 = OpenRouter 自动路由",
                )
                llm_base_url = gr.Textbox(label="base_url", value=defaults.llm_base_url)
                plan_btn = gr.Button("Run Plan", variant="primary")

            with gr.Tab("阶段二 · 准备与中间产物"):
                snapshot_path = gr.Textbox(
                    label="snapshot 路径",
                    placeholder="output/episodes/ep_xxx/plans/ep_xxx.json",
                )
                music_dir = gr.Textbox(label="music_dir", value=defaults.music_dir)
                tts_provider = gr.Dropdown(
                    label="tts_provider",
                    choices=["default", "edge", "elevenlabs", "minimax"],
                    value="default",
                )
                with gr.Row():
                    crossfade_seconds = gr.Number(
                        label="crossfade_seconds（歌→歌）",
                        value=defaults.crossfade_seconds,
                    )
                    voice_music_crossfade_seconds = gr.Number(
                        label="voice_music_crossfade_seconds（串词→歌下限）",
                        value=defaults.voice_music_crossfade_seconds,
                    )
                intro_align_enabled = gr.Checkbox(
                    label="voice_music_intro_align_enabled",
                    value=defaults.intro_align_enabled,
                )
                intro_align_max_seconds = gr.Number(
                    label="voice_music_intro_align_max_seconds",
                    value=defaults.intro_align_max_seconds,
                )
                stage2_btn = gr.Button("Run Stage 2", variant="primary")
                create_btn = gr.Button("One-shot Create（跳过人工改 mix_params）")

            with gr.Tab("阶段三 · 最终混音与导出"):
                mix_params_path = gr.Textbox(
                    label="mix_params JSON",
                    placeholder="output/episodes/ep_xxx/mix_params/ep_xxx_mix_params.json",
                )
                stage3_btn = gr.Button("Run Stage 3", variant="primary")

        # 观察区在 Tabs 外：切换阶段后仍能看到上次 Run 的路径/日志/音频。
        # snapshot / mix_params 各只有一份控件（分属阶段二/三），Run 回填同一组件。
        gr.Markdown("### 观察面板")
        status_md = gr.Markdown("### Pipeline Status\n⚪ idle")
        summary_md = gr.Markdown("")
        artifacts_md = gr.Markdown("_（尚无产物路径）_")
        error_box = gr.Textbox(label="错误", lines=4, interactive=False)
        audio_out = gr.Audio(label="音频预览（final mp3 / 若存在）", type="filepath", interactive=False)
        show_notes_box = gr.Textbox(label="Show Notes", lines=8, interactive=False)
        logs_box = gr.Textbox(label="日志", lines=12, interactive=False)

        form_inputs = [
            topic,
            duration_minutes,
            language,
            agent_mode,
            output_dir,
            llm_model,
            openrouter_provider,
            llm_base_url,
            snapshot_path,
            music_dir,
            tts_provider,
            mix_params_path,
            crossfade_seconds,
            voice_music_crossfade_seconds,
            intro_align_enabled,
            intro_align_max_seconds,
        ]
        result_outputs = [
            status_md,
            summary_md,
            artifacts_md,
            error_box,
            audio_out,
            show_notes_box,
            logs_box,
            snapshot_path,
            mix_params_path,
        ]

        def on_plan(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("plan", params, run_plan)

        def on_stage2(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("stage2", params, run_stage2)

        def on_stage3(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("stage3", params, run_stage3)

        def on_create(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("create", params, run_create)

        plan_btn.click(on_plan, inputs=form_inputs, outputs=result_outputs)
        stage2_btn.click(on_stage2, inputs=form_inputs, outputs=result_outputs)
        stage3_btn.click(on_stage3, inputs=form_inputs, outputs=result_outputs)
        create_btn.click(on_create, inputs=form_inputs, outputs=result_outputs)

        def on_save(name: str, out_dir: str, *args: Any):
            params = _params_from_form(*args)
            try:
                path = save_preset(out_dir or params.output_dir, name, params.as_form_dict())
                names = list_preset_names(out_dir or params.output_dir)
                return f"已保存 `{path}`", gr.Dropdown(choices=names, value=Path(path).stem)
            except Exception as exc:  # noqa: BLE001
                return f"保存失败：{exc}", gr.update()

        def on_load(name: str, out_dir: str, *args: Any):
            params = _params_from_form(*args)
            if not (name or "").strip():
                return [*(gr.update() for _ in form_inputs), "请先在下拉框选择快照。"]
            try:
                loaded = load_preset_by_name(out_dir or params.output_dir, name)
            except Exception as exc:  # noqa: BLE001
                return [*(gr.update() for _ in form_inputs), f"加载失败：{exc}"]
            merged = params.as_form_dict()
            merged.update(loaded)
            updates = [
                merged["topic"],
                merged["duration_minutes"],
                merged["language"],
                merged["agent_mode"],
                merged["output_dir"],
                merged["llm_model"],
                merged["openrouter_provider"],
                merged["llm_base_url"],
                merged["snapshot_path"],
                merged["music_dir"],
                merged["tts_provider"],
                merged["mix_params_path"],
                merged["crossfade_seconds"],
                merged["voice_music_crossfade_seconds"],
                merged["intro_align_enabled"],
                merged["intro_align_max_seconds"],
            ]
            return [*updates, f"已加载 `{name}`"]

        def on_refresh(out_dir: str):
            names = list_preset_names(out_dir or defaults.output_dir)
            return gr.Dropdown(choices=names), f"共 {len(names)} 个快照"

        save_btn.click(
            on_save,
            inputs=[preset_name, output_dir, *form_inputs],
            outputs=[preset_msg, preset_pick],
        )
        load_btn.click(
            on_load,
            inputs=[preset_pick, output_dir, *form_inputs],
            outputs=[*form_inputs, preset_msg],
        )
        refresh_btn.click(on_refresh, inputs=[output_dir], outputs=[preset_pick, preset_msg])

    return demo


def launch_console(*, host: str = "127.0.0.1", port: int = 7860, inbrowser: bool = True) -> None:
    try:
        import gradio as gr  # noqa: F401
    except ImportError as exc:
        raise ConsoleLaunchError(
            "未安装 gradio，无法启动 Developer Console。请执行：pip install -e ."
        ) from exc

    # 本地调试工具默认关闭 Gradio 遥测（launch 参数在部分版本无效）。
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

    if not (1 <= int(port) <= 65535):
        raise ConsoleLaunchError(f"端口非法：{port}（应为 1–65535）")
    if _is_port_listening(host, port):
        raise ConsoleLaunchError(
            f"端口已被占用：{host}:{port}。请换一个端口，例如：podcast-ai console --port 7861"
        )

    try:
        demo = build_app()
    except ConsoleLaunchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConsoleLaunchError(f"组装 Console UI 失败：{exc}") from exc

    settings = load_settings()
    allowed: list[str] = []
    for raw in (Path.cwd(), Path(settings.app.output_dir), Path(settings.app.music_dir)):
        try:
            allowed.append(str(Path(raw).expanduser().resolve()))
        except OSError:
            continue

    try:
        demo.queue()
        launch_kwargs: dict = {
            "server_name": host,
            "server_port": int(port),
            "inbrowser": inbrowser,
            "allowed_paths": allowed,
        }
        try:
            demo.launch(**launch_kwargs, analytics_enabled=False)
        except TypeError:
            demo.launch(**launch_kwargs)
    except OSError as exc:
        raise ConsoleLaunchError(f"无法绑定 {host}:{port}：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ConsoleLaunchError(f"启动 Developer Console 失败：{exc}") from exc

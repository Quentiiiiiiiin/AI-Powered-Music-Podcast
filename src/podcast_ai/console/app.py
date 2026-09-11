"""Gradio Blocks：只装配表单与展示，业务调用 runner → pipeline。"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any, Iterator

from podcast_ai.console.mix_params_timeline import (
    apply_vm_edits,
    find_mix_node,
    format_mix_timeline_markdown,
    load_mix_params_timeline,
    mix_node_choice_pairs,
    mix_timeline_from_dict,
    save_mix_params_as_new_file,
    timeline_source_as_mix_params,
)
from podcast_ai.console.presets import list_preset_names, load_preset_by_name, save_preset
from podcast_ai.console.run_progress import progress_from_events
from podcast_ai.console.runner import (
    ConsoleParams,
    ConsoleRunResult,
    defaults_from_settings,
    run_create,
    run_plan,
    run_stage2,
    run_stage3,
    settings_from_params,
)
from podcast_ai.console.snapshot_timeline import (
    TABLE_HEADERS,
    format_timeline_markdown,
    load_snapshot_timeline,
    save_timeline_as_new_snapshot,
    table_rows_to_timeline,
    timeline_from_dict,
    timeline_to_table_rows,
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


_INSIGHT_IDLE = (
    "**orchestration_mode**: `—`  \n"
    "**iteration**: `—`  \n"
    "**stage**: `—`  \n"
    "**revision**: `—`  \n"
    "**当前 Agent**: `—`  \n"
    "**失败原因**: `—`"
)


def _insight_md(
    result: ConsoleRunResult,
    *,
    agent_mode: str = "",
    orchestration_mode: str = "",
) -> str:
    """阶段一运行态：mode / iteration / stage / revision / Agent / 失败原因。"""
    if result.status == "idle":
        return _INSIGHT_IDLE
    it = result.plan_iteration
    agent = result.plan_current_agent
    mode = (agent_mode or "").strip()
    orch = (result.orchestration_mode or orchestration_mode or "").strip() or "—"
    stage = result.plan_stage
    revision = result.plan_revision
    if mode == "single_agent":
        orch_disp = f"{orch}（不应用）" if orch != "—" else "N/A"
        it_disp = "N/A" if it is None else str(it)
        agent_disp = agent or "single_agent"
        stage_disp = "N/A"
        rev_disp = "N/A"
    elif result.status == "running" and it is None and not agent and not stage:
        orch_disp = orch
        it_disp = "…"
        agent_disp = "…"
        stage_disp = "…"
        rev_disp = "…"
    else:
        orch_disp = orch
        it_disp = "N/A" if it is None else str(it)
        agent_disp = agent or "N/A"
        stage_disp = stage or "N/A"
        rev_disp = "N/A" if revision is None else str(revision)
    if result.status == "error":
        fail = (result.error or "").strip() or "未知错误"
    else:
        fail = "—"
    return (
        f"**orchestration_mode**: `{orch_disp}`  \n"
        f"**iteration**: `{it_disp}`  \n"
        f"**stage**: `{stage_disp}`  \n"
        f"**revision**: `{rev_disp}`  \n"
        f"**当前 Agent**: `{agent_disp}`  \n"
        f"**失败原因**: {fail}"
    )


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


def _yield_plan(params: ConsoleParams) -> Iterator[tuple[Any, ...]]:
    """计划运行：先标 running，再按进度钩子刷新洞察，最后给出完整结果。"""
    sink: list[dict[str, Any]] = []
    running = ConsoleRunResult.running("plan")
    orch = str(settings_from_params(params).app.orchestration_mode)
    running.orchestration_mode = orch
    packed = _ui_pack(running, params.snapshot_path, params.mix_params_path)
    insight = _insight_md(running, agent_mode=params.agent_mode, orchestration_mode=orch)
    yield (*packed, insight, insight)

    holder: list[ConsoleRunResult] = []

    def _work() -> None:
        holder.append(run_plan(params, progress_sink=sink))

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    while True:
        worker.join(timeout=0.8)
        if not worker.is_alive():
            break
        hooked = progress_from_events(sink)
        running.plan_iteration = hooked.iteration
        running.plan_current_agent = hooked.current_agent
        running.plan_stage = hooked.stage
        running.plan_revision = hooked.revision
        if params.agent_mode == "single_agent" and not running.plan_current_agent:
            running.plan_current_agent = "single_agent"
        packed = _ui_pack(running, params.snapshot_path, params.mix_params_path)
        insight = _insight_md(running, agent_mode=params.agent_mode, orchestration_mode=orch)
        yield (*packed, insight, insight)

    if not holder:
        result = ConsoleRunResult(status="error", command="plan", error="计划线程异常退出")
        result.orchestration_mode = orch
    else:
        result = holder[0]
        if not result.orchestration_mode:
            result.orchestration_mode = orch
    packed = _ui_pack(result, params.snapshot_path, params.mix_params_path)
    insight = _insight_md(result, agent_mode=params.agent_mode, orchestration_mode=orch)
    yield (*packed, insight, insight)


def _dataframe_rows(value: Any) -> list[list[Any]]:
    if value is None:
        return []
    if hasattr(value, "columns"):
        filled = value.fillna("")
        rows: list[list[Any]] = []
        for _, rec in filled.iterrows():
            rows.append([rec[h] if h in filled.columns else "" for h in TABLE_HEADERS])
        return rows
    if isinstance(value, list):
        out: list[list[Any]] = []
        for row in value:
            if isinstance(row, dict):
                out.append([row.get(h, "") for h in TABLE_HEADERS])
            else:
                out.append(list(row))
        return out
    return []


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
                orch_now = load_settings().app.orchestration_mode
                gr.Markdown(
                    f"**orchestration_mode**: `{orch_now}`"
                    "（来自 `config.yaml`，只读；仅 `multi_agent` 生效）"
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
                gr.Markdown("#### 运行态洞察")
                plan_insight_md = gr.Markdown(_INSIGHT_IDLE)

                with gr.Accordion("Snapshot 可读编辑", open=False):
                    editor_snapshot_path = gr.Textbox(
                        label="Snapshot 路径",
                        placeholder="output/episodes/ep_xxx/plans/ep_xxx.json",
                    )
                    with gr.Row():
                        load_snap_btn = gr.Button("加载时间线")
                        save_snap_btn = gr.Button("保存为同目录新文件")
                    editor_suffix = gr.Textbox(
                        label="新文件后缀（可选）",
                        placeholder="留空则用 _edited_{UTC时间戳}",
                    )
                    editor_msg = gr.Markdown("")
                    timeline_md = gr.Markdown("_尚未加载 snapshot_")
                    timeline_df = gr.Dataframe(
                        headers=TABLE_HEADERS,
                        value=[],
                        interactive=True,
                        wrap=True,
                        row_count=1,
                        label="可编辑：串词文本 / track / artist（不要改 kind 与 track_index）",
                    )
                    timeline_state = gr.State({"doc": None, "source": ""})

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

                with gr.Accordion("MixParams 时间线试听 / vm_seconds", open=False):
                    mix_editor_path = gr.Textbox(
                        label="MixParams JSON 路径",
                        placeholder="output/episodes/ep_xxx/mix_params/ep_xxx_mix_params.json",
                    )
                    with gr.Row():
                        load_mix_btn = gr.Button("加载时间线")
                        save_mix_btn = gr.Button("保存为同目录新文件")
                    mix_suffix = gr.Textbox(
                        label="新文件后缀（可选）",
                        placeholder="留空则用 _edited_{UTC时间戳}",
                    )
                    mix_tl_msg = gr.Markdown("")
                    mix_tl_md = gr.Markdown("_尚未加载 mix_params_")
                    mix_node_pick = gr.Dropdown(
                        label="选中节点",
                        choices=[],
                        value=None,
                        allow_custom_value=False,
                    )
                    mix_node_hint = gr.Markdown("")
                    mix_node_audio = gr.Audio(
                        label="节点试听（可拖动进度条）",
                        type="filepath",
                        interactive=False,
                    )
                    mix_vm_seconds = gr.Number(
                        label="vm_seconds（仅转场节点可编辑）",
                        value=None,
                        minimum=0,
                        interactive=False,
                    )
                    mix_vm_meta = gr.Markdown("—")
                    mix_tl_state = gr.State(
                        {"doc": None, "source": "", "edits": {}, "selected": None}
                    )

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
        obs_insight_md = gr.Markdown(_INSIGHT_IDLE)
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
            editor_snapshot_path,
            mix_editor_path,
        ]
        plan_outputs = [*result_outputs, plan_insight_md, obs_insight_md]

        def on_plan(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_plan(params)

        def on_stage2(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("stage2", params, run_stage2)

        def on_stage3(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("stage3", params, run_stage3)

        def on_create(*args: Any):
            params = _params_from_form(*args)
            yield from _yield_run("create", params, run_create)

        plan_btn.click(on_plan, inputs=form_inputs, outputs=plan_outputs)
        stage2_btn.click(on_stage2, inputs=form_inputs, outputs=result_outputs)
        stage3_btn.click(on_stage3, inputs=form_inputs, outputs=result_outputs)
        create_btn.click(on_create, inputs=form_inputs, outputs=result_outputs)

        def on_load_snapshot(path: str):
            try:
                file_path = Path((path or "").strip())
                if not str(file_path).strip() or str(file_path) in {".", ""}:
                    raise ValueError("请填写 snapshot 路径。")
                if not file_path.is_file():
                    raise ValueError(f"文件不存在：{file_path}")
                doc = load_snapshot_timeline(file_path)
                return (
                    format_timeline_markdown(doc),
                    timeline_to_table_rows(doc),
                    {"doc": doc.to_dict(), "source": str(file_path)},
                    f"已加载 `{file_path}`",
                )
            except Exception as exc:  # noqa: BLE001
                return (
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    f"加载失败：{exc}",
                )

        def on_save_snapshot(path: str, suffix: str, table: Any, state: dict[str, Any] | None):
            try:
                state = state or {}
                source_raw = (state.get("source") or path or "").strip()
                if not source_raw:
                    raise ValueError("请先加载 snapshot。")
                source = Path(source_raw)
                raw_doc = state.get("doc")
                if not isinstance(raw_doc, dict):
                    raise ValueError("没有可保存的时间线，请先加载。")
                base = timeline_from_dict(raw_doc)
                doc = table_rows_to_timeline(base, _dataframe_rows(table))
                dest: Path | None = None
                extra = (suffix or "").strip()
                if extra:
                    dest = source.parent / f"{source.stem}_edited_{extra}.json"
                out = save_timeline_as_new_snapshot(doc, source, dest_path=dest)
                return (
                    format_timeline_markdown(doc),
                    f"已保存 `{out}`（未覆盖源文件）",
                    str(out),
                    str(out),
                    {"doc": doc.to_dict(), "source": str(out)},
                )
            except Exception as exc:  # noqa: BLE001
                return (
                    gr.update(),
                    f"保存失败：{exc}",
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )

        load_snap_btn.click(
            on_load_snapshot,
            inputs=[editor_snapshot_path],
            outputs=[timeline_md, timeline_df, timeline_state, editor_msg],
        )
        save_snap_btn.click(
            on_save_snapshot,
            inputs=[editor_snapshot_path, editor_suffix, timeline_df, timeline_state],
            outputs=[
                timeline_md,
                editor_msg,
                snapshot_path,
                editor_snapshot_path,
                timeline_state,
            ],
        )

        def _mix_state_or_empty(state: dict[str, Any] | None) -> dict[str, Any]:
            return dict(state or {"doc": None, "source": "", "edits": {}, "selected": None})

        def _flush_vm(
            state: dict[str, Any],
            node_id: str | None,
            vm: float | None,
        ) -> dict[str, Any]:
            raw_doc = state.get("doc")
            if not isinstance(raw_doc, dict) or not node_id:
                return state
            doc = mix_timeline_from_dict(raw_doc)
            item = find_mix_node(doc, node_id)
            if item is None or item.kind != "transition" or not item.voice_segment_id:
                return state
            if vm is None:
                return state
            edits = dict(state.get("edits") or {})
            edits[item.voice_segment_id] = float(vm)
            state["edits"] = edits
            return state

        def on_load_mix(path: str):
            try:
                file_path = Path((path or "").strip())
                if not str(file_path).strip() or str(file_path) in {".", ""}:
                    raise ValueError("请填写 MixParams JSON 路径。")
                if not file_path.is_file():
                    raise ValueError(f"文件不存在：{file_path}")
                doc = load_mix_params_timeline(file_path)
                pairs = mix_node_choice_pairs(doc)
                state = {
                    "doc": doc.to_dict(),
                    "source": str(file_path),
                    "edits": {},
                    "selected": None,
                }
                return (
                    format_mix_timeline_markdown(doc),
                    gr.Dropdown(choices=pairs, value=None),
                    state,
                    f"已加载 `{file_path}` · {len(doc.items)} 个节点",
                    None,
                    "",
                    gr.update(value=None, interactive=False),
                    "—",
                )
            except Exception as exc:  # noqa: BLE001
                return (
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    f"加载失败：{exc}",
                    None,
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )

        def on_select_mix_node(node_id: str | None, vm: float | None, state: dict[str, Any] | None):
            state = _mix_state_or_empty(state)
            prev = state.get("selected")
            if isinstance(prev, str):
                state = _flush_vm(state, prev, vm)
            state["selected"] = node_id
            raw_doc = state.get("doc")
            if not isinstance(raw_doc, dict):
                return (
                    None,
                    "请先加载 mix_params。",
                    gr.update(value=None, interactive=False),
                    "—",
                    state,
                    gr.update(),
                )
            doc = mix_timeline_from_dict(raw_doc)
            edits = dict(state.get("edits") or {})
            md = format_mix_timeline_markdown(doc, edits=edits)
            item = find_mix_node(doc, node_id)
            if item is None:
                return (
                    None,
                    "未选中节点。",
                    gr.update(value=None, interactive=False),
                    "—",
                    state,
                    md,
                )
            if item.kind in {"music", "voice"}:
                audio = Path(item.audio_path) if item.audio_path else None
                if audio is None or not audio.is_file():
                    return (
                        None,
                        f"音频文件不存在：{item.audio_path}",
                        gr.update(value=None, interactive=False),
                        "—",
                        state,
                        md,
                    )
                kind_label = "音乐" if item.kind == "music" else "串词"
                return (
                    str(audio.resolve()),
                    f"试听{kind_label}节点 `{item.node_id}`（可用进度条定位）",
                    gr.update(value=None, interactive=False),
                    "—",
                    state,
                    md,
                )
            vid = item.voice_segment_id or ""
            current_vm = edits.get(vid, item.vm_seconds)
            meta = (
                f"**vm_candidate_seconds**: `{item.vm_candidate_seconds}`  \n"
                f"**intro_seconds**: `{item.intro_seconds}`  \n"
                f"**confidence**: `{item.confidence}`  \n"
                f"**reason**: {item.reason or '—'}"
            )
            return (
                None,
                "转场节点没有独立音频，请编辑下方 `vm_seconds`。",
                gr.update(value=current_vm, interactive=True),
                meta,
                state,
                md,
            )

        def on_save_mix(path: str, suffix: str, node_id: str | None, vm: float | None, state: dict[str, Any] | None):
            try:
                state = _flush_vm(_mix_state_or_empty(state), node_id, vm)
                source_raw = (state.get("source") or path or "").strip()
                if not source_raw:
                    raise ValueError("请先加载 MixParams JSON。")
                raw_doc = state.get("doc")
                if not isinstance(raw_doc, dict):
                    raise ValueError("没有可保存的时间线，请先加载。")
                doc = mix_timeline_from_dict(raw_doc)
                model = apply_vm_edits(timeline_source_as_mix_params(doc), dict(state.get("edits") or {}))
                source = Path(source_raw)
                dest: Path | None = None
                extra = (suffix or "").strip()
                if extra:
                    dest = source.parent / f"{source.stem}_edited_{extra}.json"
                out = save_mix_params_as_new_file(model, source, dest_path=dest)
                new_doc = load_mix_params_timeline(out)
                pairs = mix_node_choice_pairs(new_doc)
                new_state = {
                    "doc": new_doc.to_dict(),
                    "source": str(out),
                    "edits": {},
                    "selected": None,
                }
                return (
                    format_mix_timeline_markdown(new_doc),
                    f"已保存 `{out}`（未覆盖源文件）",
                    str(out),
                    str(out),
                    new_state,
                    gr.Dropdown(choices=pairs, value=None),
                    None,
                    gr.update(value=None, interactive=False),
                    "—",
                )
            except Exception as exc:  # noqa: BLE001
                return (
                    gr.update(),
                    f"保存失败：{exc}",
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )

        load_mix_btn.click(
            on_load_mix,
            inputs=[mix_editor_path],
            outputs=[
                mix_tl_md,
                mix_node_pick,
                mix_tl_state,
                mix_tl_msg,
                mix_node_audio,
                mix_node_hint,
                mix_vm_seconds,
                mix_vm_meta,
            ],
        )
        mix_node_pick.change(
            on_select_mix_node,
            inputs=[mix_node_pick, mix_vm_seconds, mix_tl_state],
            outputs=[
                mix_node_audio,
                mix_node_hint,
                mix_vm_seconds,
                mix_vm_meta,
                mix_tl_state,
                mix_tl_md,
            ],
        )
        save_mix_btn.click(
            on_save_mix,
            inputs=[mix_editor_path, mix_suffix, mix_node_pick, mix_vm_seconds, mix_tl_state],
            outputs=[
                mix_tl_md,
                mix_tl_msg,
                mix_params_path,
                mix_editor_path,
                mix_tl_state,
                mix_node_pick,
                mix_node_audio,
                mix_vm_seconds,
                mix_vm_meta,
            ],
        )

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

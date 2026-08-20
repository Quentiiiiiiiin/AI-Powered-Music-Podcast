from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Literal, Optional, Tuple

from pydantic import ValidationError

from podcast_ai.core.exceptions import PlanMappingError, PodcastAIError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.core.models import (
    AudioRenderConfig,
    EpisodePlan,
    EpisodeRequest,
    EpisodeResult,
    EpisodeSegment,
    MixParamsJSON,
    MixPlanSegment,
    PlaylistItem,
    Stage2Snapshot,
)
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.tts_client import TTSClient
from podcast_ai.infra.storage.paths import (
    build_episode_snapshot_from_state,
    generate_episode_id,
    generate_plan_id,
    get_final_audio_path,
    get_mix_output_path,
    get_mix_params_output_path,
    load_episode_plan,
    save_episode_plan,
    save_episode_state_snapshot,
    save_state_json,
)
from podcast_ai.modules.exporter.exporter import Exporter
from podcast_ai.modules.library.scanner import LibraryScanner
from podcast_ai.modules.mastering.processor import MasteringService
from podcast_ai.modules.mixing.mixer import Mixer
from podcast_ai.modules.selection.selector import (
    compute_segment_boundaries_from_snapshot,
    select_tracks_by_snapshot,
    split_tracks_by_snapshot,
)
from podcast_ai.modules.theme.llm_planner import ThemePlanner
from podcast_ai.modules.theme.state import validate_episode_snapshot_subset, validate_state_conforms_to_schema
from podcast_ai.modules.voiceover.tts_service import VoiceoverService

logger = logging.getLogger(__name__)


def save_plan_to_disk(
    plan: EpisodePlan,
    settings: Settings | None = None,
    episode_id: str | None = None,
    plan_id: str | None = None,
) -> Path:
    """
    将 EpisodePlan 保存到磁盘，并返回规划文件路径。

    - 若未显式提供 settings，则自动 load_settings()
    - 若未显式提供 episode_id / plan_id，则自动生成
    """
    effective_settings = settings or load_settings()
    ep_id = episode_id or generate_episode_id()
    pl_id = plan_id or generate_plan_id(ep_id)
    output_dir = Path(effective_settings.app.output_dir)
    return save_episode_plan(plan=plan, output_dir=output_dir, episode_id=ep_id, plan_id=pl_id)


def load_plan_from_disk(path: Path) -> EpisodePlan:
    """从磁盘加载 EpisodePlan（供 create-episode 阶段继续制作）。"""
    return load_episode_plan(path)


def _load_stage2_snapshot(path: Path) -> Stage2Snapshot:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise PodcastAIError(f"读取 snapshot 文件失败：{path}") from exc
    try:
        return Stage2Snapshot.model_validate_json(raw)
    except ValidationError as exc:
        raise PodcastAIError(f"snapshot 结构校验失败：{exc}") from exc


def _episode_plan_from_snapshot(snapshot: Stage2Snapshot) -> EpisodePlan:
    """
    为 exporter/show notes 复用而做的最小映射。
    阶段二主流程不再以 EpisodePlan 作为输入契约。
    """
    segments: list[EpisodeSegment] = []
    for seg in snapshot.segments:
        segments.append(
            EpisodeSegment(
                name=seg.name,
                target_duration_seconds=seg.target_duration_seconds,
                host_script=seg.script.segment_intro or "",
                target_playlist=[
                    PlaylistItem(
                        segment_name=seg.name,
                        recommended_tracks=[f"{item.track} - {item.artist}"],
                        search_hints={},
                    )
                    for item in seg.playlists
                ],
            ),
        )
    return EpisodePlan(
        segments=segments,
        target_duration_seconds=snapshot.meta.target_duration_seconds,
        style_description=snapshot.meta.theme,
        plan_id=snapshot.meta.request_id,
    )


def plan_episode(
    request: EpisodeRequest,
    settings: Settings | None = None,
    agent_mode: Literal["single_agent", "multi_agent"] = "multi_agent",
    on_progress: Callable[[dict], None] | None = None,
) -> Tuple[EpisodePlan, Path, Path]:
    """
    阶段一：调用 ThemePlanner 生成 EpisodePlan（内存对象），并落盘 state.json + {episode_id}.json。

    不再落盘 ``plans/<plan_id>.json``；阶段二仍可通过 ``save_plan_to_disk`` 等路径单独生成 EpisodePlan JSON。

    返回：(plan, state_json_path, episode_snapshot_json_path)。
    其中 `state_json_path` 为完整 state；`episode_snapshot_json_path` 为 v3.8 子集文件（非完整副本）。

    on_progress：v5.2 可选运行态钩子；默认 None，行为与现网一致。
    """
    effective_settings = settings or load_settings()
    with log_timing(logger, "plan_episode"):
        # 输出目录优先使用 request 中的设置，否则回落到全局配置
        output_dir = Path(request.output_dir or effective_settings.app.output_dir)

        planner = ThemePlanner(settings=effective_settings)
        plan, state = planner.generate_plan_and_state(
            request,
            agent_mode=agent_mode,
            on_progress=on_progress,
        )
        validate_state_conforms_to_schema(state, agent_mode=agent_mode)

        episode_id = generate_episode_id()
        plan_id = generate_plan_id(episode_id)

        # 内存中保留 plan_id，便于 CLI 摘要与后续若需单独落盘 EpisodePlan
        plan = plan.model_copy(update={"plan_id": plan_id})

        state_json_path = save_state_json(state=state, output_dir=output_dir, episode_id=episode_id)
        snapshot = build_episode_snapshot_from_state(state)
        validate_episode_snapshot_subset(snapshot)
        snapshot_json_path = save_episode_state_snapshot(
            state=state,
            output_dir=output_dir,
            episode_id=episode_id,
            snapshot=snapshot,
        )
        return plan, state_json_path, snapshot_json_path


def create_episode(
    snapshot_path: Path,
    music_dir: Path,
    settings: Settings | None = None,
    topic: str | None = None,
    language: str = "zh",
    tts_client: Optional[TTSClient] = None,
    tts_provider: Literal["edge", "elevenlabs", "minimax"] | None = None,
) -> EpisodeResult:
    """
    阶段二：从 `<episode_id>.json`（Stage2Snapshot）继续，扫描音乐库 → 选曲 → 主持 TTS → 混音 → 母带 → 导出。

    v1.3 流程：选曲（plan 驱动）→ 计算 segment 实际边界 → 主持（按边界插入）→ 混音（按 segment 分组）。
    若任一 segment 映射失败或边界缺失，直接报错并阻断，不进入混音，避免错位输出。

    v1.4：可注入 `tts_client`（如单测 mock）；默认使用 `VoiceoverService` 内建的 `get_default_tts_client`（ElevenLabs）。
    """
    effective_settings = settings or load_settings()
    if tts_provider is not None:
        # 命令级覆盖，仅覆盖 provider 本身，其他字段继续来自现有配置/.env。
        effective_settings = effective_settings.model_copy(
            deep=True,
            update={
                "tts": effective_settings.tts.model_copy(update={"provider": tts_provider}),
            },
        )

    with log_timing(logger, "create_episode"):
        snapshot = _load_stage2_snapshot(snapshot_path)

        # 从 snapshot_path 推导 episode_root：.../episodes/ep_xxx/plans/ep_xxx.json
        episode_root = snapshot_path.parent.parent
        episode_id = episode_root.name

        config = AudioRenderConfig(
            crossfade_seconds=effective_settings.audio.crossfade_seconds,
            loudness_target_lufs=effective_settings.audio.loudness_target_lufs,
            bitrate="320k",
            voice_music_crossfade_seconds=effective_settings.audio.voice_music_crossfade_seconds,
            voice_music_intro_align_enabled=effective_settings.audio.voice_music_intro_align_enabled,
            voice_music_intro_align_max_seconds=effective_settings.audio.voice_music_intro_align_max_seconds,
        )

        with log_timing(logger, "scan_library"):
            scanner = LibraryScanner(settings=effective_settings)
            library = scanner.scan_or_load_cache(music_dir)
        if not library:
            raise PodcastAIError(f"音乐目录为空或扫描失败：{music_dir}")

        with log_timing(logger, "select_tracks"):
            selected_tracks = select_tracks_by_snapshot(snapshot, library, config.crossfade_seconds)
        if not selected_tracks:
            raise PodcastAIError("选曲结果为空（plan 中无推荐曲目或无法映射），无法继续制作。")

        segment_boundaries = compute_segment_boundaries_from_snapshot(
            snapshot, selected_tracks, config.crossfade_seconds
        )
        if len(segment_boundaries) != len(snapshot.segments):
            raise PodcastAIError(
                f"segment 边界数量({len(segment_boundaries)})与 snapshot 段落数({len(snapshot.segments)})不一致，无法继续。"
            )
        track_groups = split_tracks_by_snapshot(snapshot, selected_tracks)

        with log_timing(logger, "generate_voiceovers"):
            voiceover_svc = VoiceoverService(settings=effective_settings, tts_client=tts_client)
            voiceovers = voiceover_svc.generate_voiceovers_from_snapshot(
                snapshot,
                selected_tracks_by_segment=track_groups,
                segment_boundaries=segment_boundaries,
                language=language,
            )

        mix_path = get_mix_output_path(episode_root, ext="wav")
        with log_timing(logger, "build_mix"):
            mixer = Mixer()
            mix_summary = mixer.build_mix(selected_tracks, voiceovers, config, mix_path)

        final_path = get_final_audio_path(episode_root, episode_id)
        with log_timing(logger, "apply_mastering"):
            mastering = MasteringService()
            mastering.apply_mastering(mix_path, final_path, config)

        with log_timing(logger, "export_episode"):
            exporter = Exporter()
            export_plan = _episode_plan_from_snapshot(snapshot)
            result = exporter.export_episode(
                final_audio_path=final_path,
                plan=export_plan,
                tracks=selected_tracks,
                episode_id=episode_id,
                actual_duration_seconds=int(mix_summary.actual_duration_seconds),
                topic=topic or snapshot.meta.theme or "Episode",
            )

        logger.info(
            "制作完成：%s | 音频：%s | 时长：%ds",
            episode_id,
            result.audio_path,
            result.actual_duration_seconds,
        )
        return result


def create_episode_stage2(
    snapshot_path: Path,
    music_dir: Path,
    settings: Settings | None = None,
    topic: str | None = None,
    language: str = "zh",
    tts_client: Optional[TTSClient] = None,
    tts_provider: Literal["edge", "elevenlabs", "minimax"] | None = None,
) -> Path:
    """
    v4.5：阶段二——生成可编辑混音参数 JSON（不导出最终音频）。
    """
    effective_settings = settings or load_settings()
    if tts_provider is not None:
        effective_settings = effective_settings.model_copy(
            deep=True,
            update={"tts": effective_settings.tts.model_copy(update={"provider": tts_provider})},
        )

    with log_timing(logger, "create_episode_stage2"):
        snapshot = _load_stage2_snapshot(snapshot_path)

        episode_root = snapshot_path.parent.parent
        episode_id = episode_root.name

        config = AudioRenderConfig(
            crossfade_seconds=effective_settings.audio.crossfade_seconds,
            loudness_target_lufs=effective_settings.audio.loudness_target_lufs,
            bitrate="320k",
            voice_music_crossfade_seconds=effective_settings.audio.voice_music_crossfade_seconds,
            voice_music_intro_align_enabled=effective_settings.audio.voice_music_intro_align_enabled,
            voice_music_intro_align_max_seconds=effective_settings.audio.voice_music_intro_align_max_seconds,
        )

        with log_timing(logger, "scan_library"):
            scanner = LibraryScanner(settings=effective_settings)
            library = scanner.scan_or_load_cache(music_dir)
        if not library:
            raise PodcastAIError(f"音乐目录为空或扫描失败：{music_dir}")

        with log_timing(logger, "select_tracks"):
            selected_tracks = select_tracks_by_snapshot(snapshot, library, config.crossfade_seconds)
        if not selected_tracks:
            raise PodcastAIError("选曲结果为空（plan 中无推荐曲目或无法映射），无法继续制作。")

        segment_boundaries = compute_segment_boundaries_from_snapshot(
            snapshot, selected_tracks, config.crossfade_seconds
        )
        if len(segment_boundaries) != len(snapshot.segments):
            raise PodcastAIError(
                f"segment 边界数量({len(segment_boundaries)})与 snapshot 段落数({len(snapshot.segments)})不一致，无法继续。"
            )
        track_groups = split_tracks_by_snapshot(snapshot, selected_tracks)

        with log_timing(logger, "generate_voiceovers"):
            voiceover_svc = VoiceoverService(settings=effective_settings, tts_client=tts_client)
            voiceovers = voiceover_svc.generate_voiceovers_from_snapshot(
                snapshot,
                selected_tracks_by_segment=track_groups,
                segment_boundaries=segment_boundaries,
                language=language,
            )

        mixer = Mixer()
        with log_timing(logger, "build_mix_params"):
            transitions = mixer.build_mix_params(selected_tracks, voiceovers, config)

        plan_segments: list[MixPlanSegment] = []
        for seg in snapshot.segments:
            plan_segments.append(
                MixPlanSegment(
                    name=seg.name,
                    target_duration_seconds=seg.target_duration_seconds,
                    mood="",
                    host_script=seg.script.segment_intro or "",
                    target_playlist=[],
                )
            )

        mix_params_json = MixParamsJSON(
            meta={
                "schema_version": "v4.5",
                "theme": topic or snapshot.meta.theme or "Episode",
                "language": snapshot.meta.language,
                "target_duration_seconds": snapshot.meta.target_duration_seconds,
                "request_id": snapshot.meta.request_id,
            },
            plan_segments=plan_segments,
            style_description=snapshot.meta.theme or "Episode",
            tracks=selected_tracks,
            voiceovers=voiceovers,
            transitions=transitions,
        )

        mix_params_path = get_mix_params_output_path(episode_root, episode_id)
        mix_params_path.parent.mkdir(parents=True, exist_ok=True)
        mix_params_path.write_text(
            mix_params_json.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info("stage2 生成 MixParamsJSON：%s", mix_params_path)
        return mix_params_path


def finalize_episode_stage3(
    mix_params_json_path: Path,
    settings: Settings | None = None,
    topic: str | None = None,
) -> EpisodeResult:
    """
    v4.5：阶段三——读取并严格校验 MixParamsJSON，完成最终混音导出。
    """
    effective_settings = settings or load_settings()
    with log_timing(logger, "finalize_episode_stage3"):
        try:
            mix_params_json_text = mix_params_json_path.read_text(encoding="utf-8")
            mix_params_json = MixParamsJSON.model_validate_json(mix_params_json_text)
        except Exception as exc:  # noqa: BLE001
            raise PodcastAIError(f"读取或校验 mix_params_json 失败：{mix_params_json_path}") from exc

        # {episode_root}/mix_params/{episode_id}_mix_params.json
        episode_root = mix_params_json_path.parent.parent
        episode_id = episode_root.name
        final_wav_path = get_mix_output_path(episode_root, ext="wav")
        final_mp3_path = get_final_audio_path(episode_root, episode_id)

        config = AudioRenderConfig(
            crossfade_seconds=effective_settings.audio.crossfade_seconds,
            loudness_target_lufs=effective_settings.audio.loudness_target_lufs,
            bitrate="320k",
            voice_music_crossfade_seconds=effective_settings.audio.voice_music_crossfade_seconds,
            voice_music_intro_align_enabled=effective_settings.audio.voice_music_intro_align_enabled,
            voice_music_intro_align_max_seconds=effective_settings.audio.voice_music_intro_align_max_seconds,
        )

        mixer = Mixer()
        try:
            with log_timing(logger, "render_final_mix"):
                mix_summary = mixer.render_final_mix_from_mix_params(
                    mix_params_json=mix_params_json,
                    config=config,
                    output_path=final_wav_path,
                )
        except Exception as exc:  # noqa: BLE001
            raise PodcastAIError(f"stage3 渲染失败：{exc}") from exc

        with log_timing(logger, "apply_mastering"):
            mastering = MasteringService()
            mastering.apply_mastering(final_wav_path, final_mp3_path, config)

        with log_timing(logger, "export_episode"):
            exporter = Exporter()
            segments: list[EpisodeSegment] = []
            for ps in mix_params_json.plan_segments:
                segments.append(
                    EpisodeSegment(
                        name=ps.name,
                        target_duration_seconds=ps.target_duration_seconds,
                        bpm_range=None,
                        mood=ps.mood or "",
                        host_script=ps.host_script or "",
                        target_playlist=ps.target_playlist,
                    )
                )
            export_plan = EpisodePlan(
                segments=segments,
                target_duration_seconds=mix_params_json.meta.target_duration_seconds,
                style_description=mix_params_json.style_description,
                plan_id=mix_params_json.meta.request_id,
            )

            result = exporter.export_episode(
                final_audio_path=final_mp3_path,
                plan=export_plan,
                tracks=mix_params_json.tracks,
                episode_id=episode_id,
                actual_duration_seconds=int(mix_summary.actual_duration_seconds),
                topic=topic or mix_params_json.meta.theme or "Episode",
            )

        logger.info("stage3 完成：%s | %s", episode_id, result.audio_path)
        return result


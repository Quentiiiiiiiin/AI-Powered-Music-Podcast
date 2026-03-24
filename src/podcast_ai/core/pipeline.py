from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

from podcast_ai.core.exceptions import PodcastAIError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.core.models import (
    AudioRenderConfig,
    EpisodePlan,
    EpisodeRequest,
    EpisodeResult,
)
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.tts_client import TTSClient
from podcast_ai.infra.storage.paths import (
    generate_episode_id,
    generate_plan_id,
    get_episode_root,
    get_final_audio_path,
    get_mix_output_path,
    get_playlist_markdown_path,
    load_episode_plan,
    save_episode_plan,
)
from podcast_ai.modules.exporter.exporter import Exporter
from podcast_ai.modules.library.scanner import LibraryScanner
from podcast_ai.modules.mastering.processor import MasteringService
from podcast_ai.modules.mixing.mixer import Mixer
from podcast_ai.modules.selection.selector import compute_segment_boundaries, select_tracks_by_plan
from podcast_ai.modules.theme.llm_planner import ThemePlanner
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


def plan_episode(
    request: EpisodeRequest,
    settings: Settings | None = None,
) -> Tuple[EpisodePlan, Path, Path]:
    """
    阶段一：调用 ThemePlanner 生成 EpisodePlan，并落盘 JSON + playlist Markdown。

    返回：(plan, plan_json_path, playlist_markdown_path)
    """
    effective_settings = settings or load_settings()
    with log_timing(logger, "plan_episode"):
        # 输出目录优先使用 request 中的设置，否则回落到全局配置
        output_dir = Path(request.output_dir or effective_settings.app.output_dir)

        planner = ThemePlanner(settings=effective_settings)
        plan = planner.generate_plan(request)

        episode_id = generate_episode_id()
        plan_id = generate_plan_id(episode_id)

        # 将 plan_id 写回模型（保持持久化与内存一致）
        plan = plan.model_copy(update={"plan_id": plan_id})

        plan_json_path = save_episode_plan(
            plan=plan,
            output_dir=output_dir,
            episode_id=episode_id,
            plan_id=plan_id,
        )

        episode_root = get_episode_root(output_dir, episode_id)
        playlist_md_path = get_playlist_markdown_path(episode_root)
        playlist_md_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        lines.append(f"# Episode Plan - {request.topic}")
        lines.append("")
        lines.append(f"- 目标时长：{request.duration_minutes} 分钟")
        lines.append(f"- 语言：{request.language}")
        lines.append(f"- Episode ID：{episode_id}")
        lines.append(f"- Plan ID：{plan_id}")
        lines.append("")

        for idx, seg in enumerate(plan.segments, start=1):
            lines.append(f"## 段落 {idx} - {seg.name}")
            lines.append("")
            lines.append(f"- 目标时长（秒）：{seg.target_duration_seconds}")
            if seg.bpm_range:
                lines.append(f"- BPM 区间：{seg.bpm_range[0]}–{seg.bpm_range[1]}")
            if seg.mood:
                lines.append(f"- 情绪：{seg.mood}")
            lines.append("")
            lines.append("### 目标歌单建议")
            if not seg.target_playlist:
                lines.append("- （无推荐条目）")
            else:
                for item in seg.target_playlist:
                    rec = "; ".join(item.recommended_tracks) if item.recommended_tracks else "（未给出曲目名称）"
                    lines.append(f"- 推荐：{rec}")
                    if item.search_hints:
                        lines.append(f"  - 搜索提示：{item.search_hints}")
            lines.append("")

        playlist_md_path.write_text("\n".join(lines), encoding="utf-8")

        return plan, plan_json_path, playlist_md_path


def create_episode(
    plan_path: Path,
    music_dir: Path,
    settings: Settings | None = None,
    topic: str | None = None,
    language: str = "zh",
    tts_client: Optional[TTSClient] = None,
) -> EpisodeResult:
    """
    阶段二：从 plan 文件继续，扫描音乐库 → 选曲 → 主持 TTS → 混音 → 母带 → 导出。

    v1.3 流程：选曲（plan 驱动）→ 计算 segment 实际边界 → 主持（按边界插入）→ 混音（按 segment 分组）。
    若任一 segment 映射失败或边界缺失，直接报错并阻断，不进入混音，避免错位输出。

    v1.4：可注入 `tts_client`（如单测 mock）；默认使用 `VoiceoverService` 内建的 `get_default_tts_client`（ElevenLabs）。
    """
    effective_settings = settings or load_settings()

    with log_timing(logger, "create_episode"):
        try:
            plan = load_episode_plan(plan_path)
        except Exception as exc:
            raise PodcastAIError(f"加载规划文件失败：{plan_path}") from exc

        # 从 plan_path 推导 episode_root：.../episodes/ep_xxx/plans/xxx.json
        episode_root = plan_path.parent.parent
        episode_id = episode_root.name

        config = AudioRenderConfig(
            crossfade_seconds=effective_settings.audio.crossfade_seconds,
            loudness_target_lufs=effective_settings.audio.loudness_target_lufs,
            bitrate="192k",
        )

        with log_timing(logger, "scan_library"):
            scanner = LibraryScanner(settings=effective_settings)
            library = scanner.scan_or_load_cache(music_dir)
        if not library:
            raise PodcastAIError(f"音乐目录为空或扫描失败：{music_dir}")

        with log_timing(logger, "select_tracks"):
            # v1.2：严格按 plan 顺序映射；映射失败时 PlanMappingError 向上抛出，阻断后续流程
            selected_tracks = select_tracks_by_plan(plan, library, config.crossfade_seconds)
        if not selected_tracks:
            raise PodcastAIError("选曲结果为空（plan 中无推荐曲目或无法映射），无法继续制作。")

        # v1.3：基于已映射歌曲计算 segment 实际边界；边界缺失时 PlanMappingError 向上抛出
        segment_boundaries = compute_segment_boundaries(
            plan, selected_tracks, config.crossfade_seconds
        )
        if len(segment_boundaries) != len(plan.segments):
            raise PodcastAIError(
                f"segment 边界数量({len(segment_boundaries)})与 plan 段落数({len(plan.segments)})不一致，无法继续。"
            )

        with log_timing(logger, "generate_voiceovers"):
            voiceover_svc = VoiceoverService(settings=effective_settings, tts_client=tts_client)
            voiceovers = voiceover_svc.generate_voiceovers(
                plan, language=language, segment_boundaries=segment_boundaries
            )

        mix_path = get_mix_output_path(episode_root, ext="wav")
        with log_timing(logger, "build_mix"):
            mixer = Mixer()
            mix_summary = mixer.build_mix(
                selected_tracks, voiceovers, config, mix_path, plan=plan
            )

        final_path = get_final_audio_path(episode_root, episode_id)
        with log_timing(logger, "apply_mastering"):
            mastering = MasteringService()
            mastering.apply_mastering(mix_path, final_path, config)

        with log_timing(logger, "export_episode"):
            exporter = Exporter()
            result = exporter.export_episode(
                final_audio_path=final_path,
                plan=plan,
                tracks=selected_tracks,
                episode_id=episode_id,
                actual_duration_seconds=int(mix_summary.actual_duration_seconds),
                topic=topic or plan.style_description or "Episode",
            )

        logger.info(
            "制作完成：%s | 音频：%s | 时长：%ds",
            episode_id,
            result.audio_path,
            result.actual_duration_seconds,
        )
        return result


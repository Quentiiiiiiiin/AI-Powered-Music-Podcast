"""
导出：生成 Show Notes 并产出 EpisodeResult。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from podcast_ai.core.models import EpisodePlan, EpisodeResult, SelectedTrack
from podcast_ai.infra.storage.paths import get_show_notes_path

logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float) -> str:
    """将秒数格式化为 MM:SS。"""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


def _build_show_notes(
    topic: str,
    plan: EpisodePlan,
    tracks: list[SelectedTrack],
    actual_duration_seconds: int,
    include_timestamps: bool = True,
) -> str:
    """构建 Show Notes Markdown 内容。"""
    lines: list[str] = []
    lines.append(f"# {topic}")
    lines.append("")
    lines.append(f"- 时长：{actual_duration_seconds // 60} 分 {actual_duration_seconds % 60} 秒")
    if plan.style_description:
        lines.append(f"- 风格：{plan.style_description}")
    lines.append("")
    lines.append("## 段落")
    for seg in plan.segments:
        lines.append(f"- **{seg.name}**：{seg.target_duration_seconds}s" + (f"，{seg.mood}" if seg.mood else ""))
    lines.append("")
    lines.append("## 曲目清单")
    for i, st in enumerate(tracks, start=1):
        title = st.track.title or st.track.file_path.name
        artist = st.track.artist or "—"
        if include_timestamps:
            ts = _format_timestamp(st.start_time_in_episode)
            lines.append(f"{i}. [{ts}] {title} - {artist}")
        else:
            lines.append(f"{i}. {title} - {artist}")
    lines.append("")
    return "\n".join(lines)


class Exporter:
    """
    导出最终 MP3 与 Show Notes，产出 EpisodeResult。
    """

    def export_episode(
        self,
        final_audio_path: Path,
        plan: EpisodePlan,
        tracks: list[SelectedTrack],
        episode_id: str,
        actual_duration_seconds: int,
        topic: Optional[str] = None,
        show_notes_path: Optional[Path] = None,
        include_timestamps: bool = True,
    ) -> EpisodeResult:
        """
        产出 EpisodeResult 并落盘 Show Notes。
        final_audio_path 应为已由 MasteringService 生成的 MP3 路径。
        """
        effective_topic = topic or plan.style_description or "Episode"
        show_notes_content = _build_show_notes(
            topic=effective_topic,
            plan=plan,
            tracks=tracks,
            actual_duration_seconds=actual_duration_seconds,
            include_timestamps=include_timestamps,
        )

        if show_notes_path is None:
            episode_root = final_audio_path.parent.parent
            show_notes_path = get_show_notes_path(episode_root, episode_id)

        show_notes_path.parent.mkdir(parents=True, exist_ok=True)
        show_notes_path.write_text(show_notes_content, encoding="utf-8")
        logger.info("已写入 Show Notes: %s", show_notes_path)

        return EpisodeResult(
            episode_id=episode_id,
            audio_path=final_audio_path,
            actual_duration_seconds=actual_duration_seconds,
            show_notes=show_notes_content,
            tracks=tracks,
        )

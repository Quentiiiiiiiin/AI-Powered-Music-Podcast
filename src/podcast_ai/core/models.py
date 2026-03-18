from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


class EpisodeRequest(BaseModel):
    """用户发起一次节目录制请求时的输入。"""

    topic: str = Field(..., description="节目主题")
    duration_minutes: int = Field(..., ge=1, description="目标时长（分钟）")
    language: Literal["zh", "en"] = Field(
        "zh",
        description="串词与 TTS 语言（目前支持 zh/en）",
    )
    output_dir: Path = Field(
        Path("./output"),
        description="本次请求的输出目录（计划文件/最终音频等）",
    )


class PlaylistItem(BaseModel):
    """目标歌单中的一条推荐，用于指导用户去各平台搜索/下载。"""

    segment_name: str
    recommended_tracks: List[str] = Field(
        default_factory=list,
        description="推荐曲目名称或描述",
    )
    search_hints: Dict[str, object] = Field(
        default_factory=dict,
        description="搜索条件：艺术家、曲风、BPM 区间、关键词等",
    )


class EpisodeSegment(BaseModel):
    """节目中的一个段落（如开场/中段/收尾）。"""

    name: str
    target_duration_seconds: int = Field(..., ge=1)
    bpm_range: Optional[Tuple[int, int]] = None
    mood: str = ""
    host_script: str = ""
    target_playlist: List[PlaylistItem] = Field(
        default_factory=list,
        description="该段的目标歌单规划",
    )


class EpisodePlan(BaseModel):
    """由主题规划模块生成的完整节目规划，可持久化为 JSON。"""

    segments: List[EpisodeSegment] = Field(default_factory=list)
    target_duration_seconds: int = Field(..., ge=1)
    overall_bpm_range: Optional[Tuple[int, int]] = None
    style_description: str = ""
    plan_id: str


class Track(BaseModel):
    """原始音轨文件及基础标签。"""

    id: str
    file_path: Path
    title: Optional[str] = None
    artist: Optional[str] = None


class TrackMetadata(BaseModel):
    """与音轨关联的可计算元数据。"""

    track_id: str
    duration_seconds: float
    bpm: Optional[float] = None
    genre: Optional[str] = None


class TrackWithMetadata(BaseModel):
    """音轨及其元数据的组合，供选曲/混音等模块使用。"""

    track: Track
    metadata: TrackMetadata


class SelectedTrack(BaseModel):
    """经过选曲与排布后，在节目时间线上的一首歌。"""

    track: Track
    start_time_in_episode: float = Field(..., ge=0)
    end_time_in_episode: float = Field(..., ge=0)
    effective_duration: float = Field(
        ...,
        ge=0,
        description="考虑 crossfade 后的有效节目占用时长",
    )


class VoiceoverSegment(BaseModel):
    """主持串词对应的语音片段及其插入策略。"""

    segment_id: str
    text: str
    audio_path: Path
    insert_time_in_episode: float = Field(..., ge=0)


class AudioRenderConfig(BaseModel):
    """音频渲染相关的统一配置。"""

    sample_rate: int = 44_100
    bitrate: str = "192k"
    crossfade_seconds: float = 8.0
    loudness_target_lufs: float = -14.0


class EpisodeResult(BaseModel):
    """完整制作流程产出的整体结果。"""

    episode_id: str
    audio_path: Path
    actual_duration_seconds: int
    show_notes: str
    tracks: List[SelectedTrack] = Field(default_factory=list)


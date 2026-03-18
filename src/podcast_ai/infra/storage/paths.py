from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path

from podcast_ai.core.models import EpisodePlan, VoiceoverSegment


def generate_episode_id() -> str:
    """生成全局唯一的 episode_id。"""
    ts = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"ep_{ts}_{short}"


def generate_plan_id(episode_id: str) -> str:
    """基于 episode_id 生成 plan_id，便于人工关联。"""
    suffix = uuid.uuid4().hex[:6]
    return f"{episode_id}_plan_{suffix}"


def get_episode_root(output_dir: Path, episode_id: str) -> Path:
    """
    单期节目的根目录：
      {output_dir}/episodes/{episode_id}/
    """
    return output_dir.joinpath("episodes", episode_id)


def get_plan_path(episode_root: Path, plan_id: str) -> Path:
    """
    规划文件路径：
      {episode_root}/plans/{plan_id}.json
    """
    return episode_root.joinpath("plans", f"{plan_id}.json")


def get_mix_dir(episode_root: Path) -> Path:
    """
    中间混音/临时音频目录：
      {episode_root}/mix/
    """
    return episode_root.joinpath("mix")


def get_mix_output_path(episode_root: Path, ext: str = "wav") -> Path:
    """
    中间混音文件路径（crossfade 拼接 + 主持叠入后的原始输出）：
      {episode_root}/mix/mix.{ext}
    """
    return get_mix_dir(episode_root) / f"mix.{ext}"


def get_final_audio_path(episode_root: Path, episode_id: str) -> Path:
    """
    最终导出音频路径：
      {episode_root}/final/{episode_id}.mp3
    （后续可根据配置决定扩展名）
    """
    return episode_root.joinpath("final", f"{episode_id}.mp3")


def get_show_notes_path(episode_root: Path, episode_id: str) -> Path:
    """
    节目说明（Show Notes）路径：
      {episode_root}/final/{episode_id}_show_notes.md
    """
    return episode_root.joinpath("final", f"{episode_id}_show_notes.md")


def get_playlist_markdown_path(episode_root: Path) -> Path:
    """
    人类可读的目标歌单 Markdown 路径：
      {episode_root}/playlist.md
    """
    return episode_root.joinpath("playlist.md")


def get_tts_cache_dir(output_dir: Path) -> Path:
    """
    TTS 缓存目录（全局共享，而不是按 episode 拆分）：
      {output_dir}/cache/tts/
    """
    return output_dir.joinpath("cache", "tts")


def get_tts_cache_file(
    cache_dir: Path,
    voice: str,
    text_hash: str,
    ext: str = "mp3",
) -> Path:
    """
    TTS 缓存文件路径：
      {cache_dir}/{voice}/{text_hash}.{ext}
    """
    safe_voice = voice or "default"
    return cache_dir.joinpath(safe_voice, f"{text_hash}.{ext}")


def save_episode_plan(
    plan: EpisodePlan,
    output_dir: Path,
    episode_id: str,
    plan_id: str,
) -> Path:
    """
    将 EpisodePlan 序列化为 JSON 并落盘，返回文件路径。

    - **不会** 自动生成 id，由调用方先生成 episode_id / plan_id
    - 若目标目录不存在，会自动创建
    """
    episode_root = get_episode_root(output_dir, episode_id)
    plan_path = get_plan_path(episode_root, plan_id)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    json_text = plan.model_dump_json(indent=2, ensure_ascii=False)
    plan_path.write_text(json_text, encoding="utf-8")
    return plan_path


def load_episode_plan(path: Path) -> EpisodePlan:
    """从 JSON 文件加载 EpisodePlan。"""
    text = path.read_text(encoding="utf-8")
    return EpisodePlan.model_validate_json(text)


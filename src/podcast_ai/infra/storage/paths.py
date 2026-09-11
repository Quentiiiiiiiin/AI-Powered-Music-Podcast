from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any

from podcast_ai.core.models import EpisodePlan, VoiceoverSegment
from podcast_ai.modules.theme.state import PlanState


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


def get_mix_params_dir(episode_root: Path) -> Path:
    """
    阶段二生成的可编辑混音参数目录：
      {episode_root}/mix_params/
    """
    return episode_root.joinpath("mix_params")


def get_mix_params_output_path(episode_root: Path, episode_id: str, *, ext: str = "json") -> Path:
    """
    阶段二输出的可编辑混音参数 JSON：
      {episode_root}/mix_params/{episode_id}_mix_params.json
    """
    return get_mix_params_dir(episode_root) / f"{episode_id}_mix_params.{ext}"


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


def get_state_path(episode_root: Path) -> Path:
    """
    阶段一输出的统一状态文件路径：
      {episode_root}/plans/state.json
    """
    return episode_root.joinpath("plans", "state.json")


def save_state_json(state: PlanState, output_dir: Path, episode_id: str) -> Path:
    """
    将 PlanState 落盘为 state.json，并返回文件路径。
    """
    episode_root = get_episode_root(output_dir, episode_id)
    state_path = get_state_path(episode_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # PlanState 约定为结构化 dict，可直接 JSON 化
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state_path


def load_state_json(path: Path) -> PlanState:
    """从 JSON 文件加载 PlanState。"""
    text = path.read_text(encoding="utf-8")
    # PlanState 为结构化 dict：直接反序列化即可
    raw: Any = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"state.json 格式错误：期望 object，但得到 {type(raw).__name__}")
    return raw


def get_episode_state_snapshot_path(episode_root: Path, episode_id: str) -> Path:
    """
    v3.8：阶段一新增的快照文件路径：
      {episode_root}/plans/{episode_id}.json
    """
    return episode_root.joinpath("plans", f"{episode_id}.json")


def build_episode_snapshot_from_state(state: PlanState) -> dict[str, Any]:
    """
    v3.8 fix：将完整 PlanState 映射为对接子集文件结构。

    产物结构：
    - schema
    - meta.{request_id, theme, language, target_duration_seconds}
    - segments[*].{segment_id, name, target_duration_seconds, playlists, script}
    """
    schema_version = state.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ValueError("snapshot.schema 映射失败：state.schema_version 缺失或非 string")

    meta = state.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("snapshot.meta 映射失败：state.meta 必须是 object")
    for key in ("request_id", "theme", "language", "target_duration_seconds"):
        if key not in meta:
            raise ValueError(f"snapshot.meta 映射失败：state.meta.{key} 缺失")

    segments_raw = state.get("segments")
    if not isinstance(segments_raw, list):
        raise ValueError("snapshot.segments 映射失败：state.segments 必须是 array")

    segments: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments_raw):
        if not isinstance(seg, dict):
            raise ValueError(f"snapshot.segments[{idx}] 映射失败：必须是 object")
        for key in ("segment_id", "name", "target_duration_seconds", "playlist", "script"):
            if key not in seg:
                raise ValueError(f"snapshot.segments[{idx}] 映射失败：state.segments[{idx}].{key} 缺失")
        segments.append(
            {
                "segment_id": seg["segment_id"],
                "name": seg["name"],
                "target_duration_seconds": seg["target_duration_seconds"],
                "playlists": seg["playlist"],
                "script": seg["script"],
            }
        )

    return {
        "schema": schema_version,
        "meta": {
            "request_id": meta["request_id"],
            "theme": meta["theme"],
            "language": meta["language"],
            "target_duration_seconds": meta["target_duration_seconds"],
        },
        "segments": segments,
    }


def save_episode_state_snapshot(
    state: PlanState,
    output_dir: Path,
    episode_id: str,
    *,
    snapshot: dict[str, Any] | None = None,
) -> Path:
    """将 v3.8 子集 snapshot 写入 {episode_id}.json，并返回文件路径。"""
    episode_root = get_episode_root(output_dir, episode_id)
    snapshot_path = get_episode_state_snapshot_path(episode_root, episode_id)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot if snapshot is not None else build_episode_snapshot_from_state(state)
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot_path


def load_episode_state_snapshot(path: Path) -> PlanState:
    """从 {episode_id}.json 加载 PlanState。"""
    text = path.read_text(encoding="utf-8")
    raw: Any = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} 格式错误：期望 object，但得到 {type(raw).__name__}")
    return raw


def get_multi_agent_audit_run_dir(base_output_dir: Path, run_id: str) -> Path:
    """
    v3.6：单次 multi-agent 规划独占审计目录，避免多次运行互相覆盖。

    约定：``{output_dir}/audit/multi_agent/{run_id}/``
    ``run_id`` 通常取 ``state[\"meta\"][\"request_id\"]``。
    """
    safe = (run_id or "unknown").replace("/", "_").replace("\\", "_").strip() or "unknown"
    return Path(base_output_dir).expanduser() / "audit" / "multi_agent" / safe


def format_audit_agent_filename(iteration: int, agent_slug: str) -> str:
    """``iteration{i}_{agent_slug}.json``；agent_slug 为 planner | music_curator | script_writer | critic。"""
    return f"iteration{int(iteration)}_{agent_slug}.json"


def format_audit_state_filename(iteration: int) -> str:
    """完整 state 快照：``iteration{i}_state.json``。"""
    return f"iteration{int(iteration)}_state.json"


def format_audit_state_partial_filename(iteration: int) -> str:
    """失败时可查：``iteration{i}_state_partial.json``（内容含 error 与本轮开始前的 state 等元数据）。"""
    return f"iteration{int(iteration)}_state_partial.json"


def format_audit_staged_agent_filename(stage: str, revision: int, agent_slug: str) -> str:
    """
    v6.0 staged 审计命名：``stage_{stage}_rev{r}_{agent_slug}.json``。

    - stage：planner | music_curator | script_writer
    - revision：0=首次生成，1/2=第 1/2 次修复
    """
    safe_stage = (stage or "unknown").replace("/", "_").replace("\\", "_").strip() or "unknown"
    return f"stage_{safe_stage}_rev{int(revision)}_{agent_slug}.json"


def format_audit_staged_state_filename(stage: str, revision: int) -> str:
    """v6.0 staged：``stage_{stage}_rev{r}_state.json``。"""
    safe_stage = (stage or "unknown").replace("/", "_").replace("\\", "_").strip() or "unknown"
    return f"stage_{safe_stage}_rev{int(revision)}_state.json"


def format_audit_staged_state_partial_filename(stage: str, revision: int) -> str:
    """v6.0 staged：``stage_{stage}_rev{r}_state_partial.json``。"""
    safe_stage = (stage or "unknown").replace("/", "_").replace("\\", "_").strip() or "unknown"
    return f"stage_{safe_stage}_rev{int(revision)}_state_partial.json"


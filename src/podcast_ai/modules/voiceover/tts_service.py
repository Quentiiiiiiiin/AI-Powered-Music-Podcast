"""
主持串词与语音生成：从 EpisodePlan.segments[].host_script 生成 TTS 音频，输出 VoiceoverSegment 列表供混音使用。

v1.1：保证 voiceovers 与 plan.segments 一一对应（len(voiceovers) == len(plan.segments)），
对无 host_script 的 segment 输出占位（零时长静音），使混音可按索引对齐「串词_i → 组_i」。
v1.3：可选接收 segment_boundaries，将 insert_time_in_episode 设为 segment_i.music_start 之前的边界
（即 串词_1=0，串词_i=boundaries[i-1].music_end）。
v1.4：默认通过 `get_default_tts_client` 使用 ElevenLabs（统一 `TTSClient.synthesize`）；TTS 配置/鉴权/
网络等失败抛出 `PodcastAIError` 子类并向上传递，不静默替换为占位，以免混音在「无串词」下继续。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.exceptions import PlanMappingError, PodcastAIError
from podcast_ai.core.models import EpisodePlan, SegmentBoundary, SelectedTrack, Stage2Snapshot, VoiceoverSegment
from podcast_ai.infra.audio_backend import export_audio
from podcast_ai.infra.config import Settings, load_settings
from podcast_ai.infra.storage.paths import get_tts_cache_dir
from podcast_ai.infra.tts_client import TTSClient, get_default_tts_client

logger = logging.getLogger(__name__)

# 语言 -> edge-tts 默认发音人（仅 provider=edge_tts 且未配置 tts.voice 时使用）
_DEFAULT_VOICE_BY_LANG = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-JennyNeural",
}


def _get_placeholder_audio_path(output_dir: Path) -> Path:
    """
    返回零时长静音占位文件的路径；若不存在则创建。
    用于无 host_script 的 segment，保证 VoiceoverSegment 与 plan.segments 一一对应。
    """
    cache_dir = get_tts_cache_dir(output_dir)
    placeholder_path = cache_dir / "_placeholder" / "silent.wav"
    if placeholder_path.exists():
        return placeholder_path
    placeholder_path.parent.mkdir(parents=True, exist_ok=True)
    # 1ms 静音，兼容各类播放器；0ms 可能导致部分格式异常
    silent = AudioSegment.silent(duration=1)
    export_audio(silent, placeholder_path, format="wav")
    return placeholder_path


class VoiceoverService:
    """
    从 EpisodePlan 的 host_script 生成语音文件，并输出 VoiceoverSegment 列表。
    v1.3：当传入 segment_boundaries 时，insert_time 严格对齐 segment 实际歌曲边界。
    """

    def __init__(
        self,
        tts_client: Optional[TTSClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._tts = tts_client or get_default_tts_client(self._settings)

    def generate_voiceovers(
        self,
        plan: EpisodePlan,
        language: str = "zh",
        use_cache: bool = True,
        segment_boundaries: Optional[list[SegmentBoundary]] = None,
    ) -> list[VoiceoverSegment]:
        """
        为 plan.segments 一一对应生成 VoiceoverSegment 列表。
        - 有 host_script：调用 TTS 生成语音
        - 无 host_script：使用零时长静音占位
        - segment_boundaries 提供时（v1.3）：insert_time 严格对齐 segment 实际边界，
          串词_i 插入在 segment_i.music_start 之前（串词_1=0，串词_i=boundaries[i-1].music_end）
        - 未提供时：沿用 target_duration_seconds 累加（向后兼容）
        """
        voice = _get_voice_for_language(language, self._settings)
        output_dir = Path(self._settings.app.output_dir)
        placeholder_path = _get_placeholder_audio_path(output_dir)
        results: list[VoiceoverSegment] = []

        for idx, seg in enumerate(plan.segments):
            segment_id = seg.name or f"seg_{idx}"
            has_script = bool(seg.host_script and seg.host_script.strip())

            # v1.3：优先使用实际边界；否则按 target_duration 累加
            if segment_boundaries is not None and len(segment_boundaries) > idx:
                insert_time = segment_boundaries[idx - 1].music_end if idx > 0 else 0.0
            else:
                insert_time = sum(s.target_duration_seconds for s in plan.segments[:idx])

            if has_script:
                try:
                    audio_path = self._tts.synthesize(
                        seg.host_script,
                        voice=voice,
                        language=language,
                        use_cache=use_cache,
                    )
                    results.append(
                        VoiceoverSegment(
                            segment_id=segment_id,
                            text=seg.host_script,
                            audio_path=audio_path,
                            insert_time_in_episode=insert_time,
                        ),
                    )
                    logger.debug("已生成语音: %s @ %.1fs", segment_id, insert_time)
                except PodcastAIError:
                    # v1.4：TTS/配置类错误必须向上抛出，供 CLI 展示清晰原因并阻断后续混音
                    raise
                except Exception as exc:  # noqa: BLE001
                    # 非预期异常仍降级占位并记录，避免进程直接崩溃；正常路径应少见
                    logger.warning("段 '%s' 语音生成出现非预期错误，使用占位: %s", seg.name, exc)
                    results.append(
                        VoiceoverSegment(
                            segment_id=segment_id,
                            text="",
                            audio_path=placeholder_path,
                            insert_time_in_episode=insert_time,
                        ),
                    )
            else:
                results.append(
                    VoiceoverSegment(
                        segment_id=segment_id,
                        text="",
                        audio_path=placeholder_path,
                        insert_time_in_episode=insert_time,
                    ),
                )

        logger.info("主持语音生成完成: %d 段（与 segments 一一对应）", len(results))
        return results

    def generate_voiceovers_from_snapshot(
        self,
        snapshot: Stage2Snapshot,
        *,
        selected_tracks_by_segment: list[list[SelectedTrack]],
        segment_boundaries: list[SegmentBoundary],
        language: str = "zh",
        use_cache: bool = True,
    ) -> list[VoiceoverSegment]:
        """
        v3.9：从 snapshot.script 生成多插点串词。
        - segment_intro：seg_0 固定 0；其余段插在上一段音乐结束边界（prev music_end）
        - between_tracks[*].after_track_index：插在本段对应歌曲后边界
        """
        if len(selected_tracks_by_segment) != len(snapshot.segments):
            raise PlanMappingError("selected_tracks_by_segment 与 snapshot.segments 长度不一致。")
        if len(segment_boundaries) != len(snapshot.segments):
            raise PlanMappingError("segment_boundaries 与 snapshot.segments 长度不一致。")

        voice = _get_voice_for_language(language, self._settings)
        output_dir = Path(self._settings.app.output_dir)
        placeholder_path = _get_placeholder_audio_path(output_dir)
        results: list[VoiceoverSegment] = []

        for seg_idx, seg in enumerate(snapshot.segments):
            music_start = segment_boundaries[seg_idx].music_start
            seg_tracks = selected_tracks_by_segment[seg_idx]
            n_tracks = len(seg_tracks)

            intro = (seg.script.segment_intro or "").strip()
            if intro:
                # 方案 A：段首串词（除第一段）锚定在上一段 music_end，
                # 避免 crossfade 时间线上被提前到上一段最后一首歌曲结束前。
                intro_insert_time = (
                    segment_boundaries[seg_idx - 1].music_end if seg_idx > 0 else 0.0
                )
                audio_path = self._tts.synthesize(
                    intro,
                    voice=voice,
                    language=language,
                    use_cache=use_cache,
                )
                results.append(
                    VoiceoverSegment(
                        segment_id=seg.segment_id,
                        text=intro,
                        audio_path=audio_path,
                        insert_time_in_episode=intro_insert_time,
                    ),
                )
            # intro 为空时跳过，不生成语音

            track_start = music_start
            for bt in seg.script.between_tracks:
                idx = bt.after_track_index
                if idx < 0 or idx >= n_tracks:
                    raise PlanMappingError(
                        f"segments[{seg_idx}].script.between_tracks.after_track_index 越界：{idx}，本段曲目数={n_tracks}"
                    )
                text = (bt.text or "").strip()
                if not text:
                    continue
                # 按“对应曲目后”插入（使用本段第 idx 首曲目的 end_time）。
                insert_time = seg_tracks[idx].end_time_in_episode if seg_tracks else track_start
                audio_path = self._tts.synthesize(
                    text,
                    voice=voice,
                    language=language,
                    use_cache=use_cache,
                )
                results.append(
                    VoiceoverSegment(
                        segment_id=f"{seg.segment_id}_after_{idx}",
                        text=text,
                        audio_path=audio_path,
                        insert_time_in_episode=insert_time,
                    ),
                )

        # 保证时间线稳定
        results.sort(key=lambda v: v.insert_time_in_episode)
        logger.info("snapshot 串词生成完成: %d 段", len(results))
        return results


def _get_voice_for_language(language: str, settings: Settings) -> str:
    """
    返回传给 TTSClient.synthesize 的 voice 参数。

    - ElevenLabs：默认用配置中的 voice_id（在客户端内解析）；此处仅在用户显式设置
      `tts.voice` 时作为 voice_id 覆盖，否则返回空串，避免把 Edge 发音人名传给 ElevenLabs。
    - Edge：优先 `tts.voice`，否则按语言选 Edge 默认发音人。
    """
    if settings.tts.voice and settings.tts.voice.strip():
        return settings.tts.voice.strip()
    prov = (settings.tts.provider or "").strip().lower()
    if prov == "elevenlabs":
        return ""
    return _DEFAULT_VOICE_BY_LANG.get(language, "zh-CN-XiaoxiaoNeural")

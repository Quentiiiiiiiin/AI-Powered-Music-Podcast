"""
混音与时间线渲染。

v2.1：串词清晰度优先——「音乐→串词」硬切；「串词→音乐」仅在音乐轨前窗做 fade_in 与串词尾重叠；
歌曲-歌曲使用 `crossfade_seconds`。

v3.9.1：有串词时仅走「曲目时间线 + insert_time」主路径：将 `SelectedTrack` 与按 `insert_time_in_episode`
排序的串词合并为严格时间递增的 (music|voice) 块序列，再按相邻块类型选转场拼接；不再先全曲
crossfade 再在成品上插点。

v4.3：`voice->music` 增加统一限幅：候选值（含 v4.2 intro 对齐）在最终应用前必须满足
`crossfade <= voice_duration`，并受配置 min/max 约束；其余边界语义不变。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Literal

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.models import (
    AudioRenderConfig,
    MixParamsJSON,
    MixParamsTransition,
    SelectedTrack,
    VoiceoverSegment,
)
from podcast_ai.infra.audio_backend import crossfade_concat, export_audio, load_audio, simple_normalize
from podcast_ai.modules.mixing.intro_align import estimate_track_intro_seconds

logger = logging.getLogger(__name__)

# 与 snapshot / segment 边界计算同源量级，用于锚点与冲突判断
_TIMELINE_EPS_SECONDS = 0.05

BlockKind = Literal["music", "voice"]


@dataclass
class MixRenderSummary:
    """混音渲染摘要。"""

    mix_path: Path
    actual_duration_seconds: float
    track_count: int
    voiceover_count: int


@dataclass
class _TimelineBlock:
    kind: BlockKind
    audio: AudioSegment
    # music 块：块内时间顺序第一首 track 的路径；voice 块此字段为 None
    first_track_path: Path | None = None
    # voice 块：该 voice 后紧邻 music 块的第一首 track 路径；music 块此字段为 None
    next_music_first_track_path: Path | None = None
    # voice 块：该串词的 segment_id；music 块此字段为 None
    voice_segment_id: str | None = None


def _join_music_to_voice_hard(music: AudioSegment, voice: AudioSegment) -> AudioSegment:
    """音乐段结束后紧接串词——不叠化、硬切（music + voice）。"""
    return music + voice


def _join_voice_to_music_fade_music_only(
    voice: AudioSegment,
    music: AudioSegment,
    vm_ms: int,
) -> AudioSegment:
    """
    串词结束后紧接音乐——仅对音乐前 vm_ms 做 fade_in 与串词尾窗重叠；串词尾部不衰减。

    总时长 = len(voice) + len(music) - vm_ms（与对称叠化总时长公式一致）。
    """
    if vm_ms <= 0 or len(voice) == 0 or len(music) == 0:
        return voice + music
    vm_ms = min(vm_ms, len(voice), len(music))
    if vm_ms <= 0:
        return voice + music
    v_pre = voice[:-vm_ms]
    v_tail = voice[-vm_ms:]
    m_head = music[:vm_ms].fade_in(vm_ms)
    m_rest = music[vm_ms:]
    overlap = v_tail.overlay(m_head)
    return v_pre + overlap + m_rest


def _anchor_times_seconds(tracks: list[SelectedTrack]) -> set[float]:
    anchors: set[float] = {0.0}
    for st in tracks:
        anchors.add(float(st.start_time_in_episode))
        anchors.add(float(st.end_time_in_episode))
    return anchors


def _is_on_anchor(t: float, anchors: set[float], eps: float) -> bool:
    return any(abs(t - a) <= eps for a in anchors)


def _validate_voiceovers_against_tracks(
    tracks: list[SelectedTrack],
    voiceovers: list[VoiceoverSegment],
    eps: float,
) -> list[tuple[int, VoiceoverSegment]]:
    """按 (insert_time, 原序) 排序；校验锚点、非曲目内部、insert_time 不冲突。"""
    if not tracks:
        raise ValueError("selected_tracks 不能为空。")
    anchors = _anchor_times_seconds(tracks)
    indexed = list(enumerate(voiceovers))
    ordered = sorted(indexed, key=lambda iv: (iv[1].insert_time_in_episode, iv[0]))
    prev_insert: float | None = None
    for _, vo in ordered:
        t = float(vo.insert_time_in_episode)
        if prev_insert is not None and abs(t - prev_insert) <= eps:
            raise ValueError(
                f"串词 insert_time 冲突（同一锚点多条）: segment_id={vo.segment_id}, insert_time={t}"
            )
        prev_insert = t
        if not _is_on_anchor(t, anchors, eps):
            raise ValueError(
                f"串词 insert_time 非合法锚点（须为 0、某曲 start/end）: "
                f"segment_id={vo.segment_id}, insert_time={t}"
            )
        # 不再用 (start,end) 开区间判断「内部」：crossfade 下相邻曲目的 episode 时间线重叠，
        # 合法锚点（如上一曲 end）可能落在下一曲 start 之后，开区间检测会误判。
    return ordered


def _load_track_segment(st: SelectedTrack) -> AudioSegment:
    return simple_normalize(load_audio(st.track.file_path), target_dbfs=-16.0)


def _load_voice_segment(vo: VoiceoverSegment) -> AudioSegment:
    try:
        return simple_normalize(load_audio(vo.audio_path), target_dbfs=-16.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载主持 %s 失败，使用空占位: %s", vo.segment_id, exc)
        return AudioSegment.silent(duration=0)


def _music_chunk_from_tracks(tracks_chunk: list[SelectedTrack], crossfade_seconds: float) -> AudioSegment:
    if not tracks_chunk:
        return AudioSegment.silent(duration=0)
    audios = [_load_track_segment(st) for st in tracks_chunk]
    if len(audios) == 1:
        return audios[0]
    return crossfade_concat(audios, crossfade_seconds)


def _build_ordered_blocks_from_tracks_and_voiceovers(
    selected_tracks: list[SelectedTrack],
    voiceovers: list[VoiceoverSegment],
    crossfade_seconds: float,
    eps: float,
) -> list[_TimelineBlock]:
    """
    将曲目（按 episode 时间线排序）与串词（按 insert_time 排序）合并为严格时间递增的块列表。

    规则：在每条串词之前，flush 所有 end_time_in_episode <= insert_time 的曲目（组内先做歌曲-歌曲
    crossfade）；串词之后由后续 flush 再接下一组曲目。
    """
    tracks_sorted = sorted(selected_tracks, key=lambda st: float(st.start_time_in_episode))
    ordered_pairs = _validate_voiceovers_against_tracks(tracks_sorted, voiceovers, eps)
    blocks: list[_TimelineBlock] = []
    track_idx = 0
    n = len(tracks_sorted)

    for _, vo in ordered_pairs:
        insert_t = float(vo.insert_time_in_episode)
        flush_queue: list[SelectedTrack] = []
        while track_idx < n and float(tracks_sorted[track_idx].end_time_in_episode) <= insert_t + eps:
            flush_queue.append(tracks_sorted[track_idx])
            track_idx += 1
        music_seg = _music_chunk_from_tracks(flush_queue, crossfade_seconds)
        if len(music_seg) > 0:
            blocks.append(
                _TimelineBlock(
                    kind="music",
                    audio=music_seg,
                    first_track_path=flush_queue[0].track.file_path,
                )
            )
        next_music_first = tracks_sorted[track_idx].track.file_path if track_idx < n else None
        blocks.append(
            _TimelineBlock(
                kind="voice",
                audio=_load_voice_segment(vo),
                next_music_first_track_path=next_music_first,
                voice_segment_id=vo.segment_id,
            )
        )

    rest = tracks_sorted[track_idx:]
    tail = _music_chunk_from_tracks(rest, crossfade_seconds)
    if len(tail) > 0:
        blocks.append(
            _TimelineBlock(
                kind="music",
                audio=tail,
                first_track_path=rest[0].track.file_path,
            )
        )
    return blocks


def _resolve_voice_music_vm_ms(
    voice_audio: AudioSegment,
    music_audio: AudioSegment,
    *,
    config: AudioRenderConfig,
    next_music_first_track_path: Path | None,
) -> int:
    """
    v4.3：voice->music 动态 vm。

    规则顺序：
    1) 先得到候选值（v4.2：intro 估计成功则放大，否则回退默认）；
    2) 再统一限幅（配置 min/max + voice/music 时长上限），保证 crossfade<=voice_duration。
    """
    base_vm_ms = int(round(config.voice_music_crossfade_seconds * 1000))
    if base_vm_ms <= 0:
        return 0

    vm_candidate_ms = base_vm_ms
    if config.voice_music_intro_align_enabled and next_music_first_track_path is not None:
        estimate = estimate_track_intro_seconds(
            next_music_first_track_path,
            max_intro_seconds=config.voice_music_intro_align_max_seconds,
        )
        if estimate.intro_seconds is not None:
            intro_ms = int(round(estimate.intro_seconds * 1000))
            cap_ms = int(round(config.voice_music_intro_align_max_seconds * 1000))
            if cap_ms > 0:
                intro_ms = min(intro_ms, cap_ms)
            if intro_ms > 0:
                # 方案 1：保持 v3.9.1 的默认 vm 作为下限，intro/cap 仅用于放大上界。
                vm_candidate_ms = max(base_vm_ms, intro_ms)
                logger.info(
                    "v4.2 intro 对齐生效: track=%s intro=%.2fs conf=%.2f vm_candidate=%dms(base=%dms)",
                    next_music_first_track_path,
                    estimate.intro_seconds,
                    estimate.confidence,
                    vm_candidate_ms,
                    base_vm_ms,
                )
            else:
                logger.info(
                    "v4.2 intro 估计过小，回退默认 vm: track=%s reason=%s",
                    next_music_first_track_path,
                    estimate.reason,
                )
        else:
            logger.warning(
                "v4.2 intro 估计失败，回退默认 vm: track=%s reason=%s",
                next_music_first_track_path,
                estimate.reason,
            )
    elif config.voice_music_intro_align_enabled:
        logger.info("v4.2 intro 对齐跳过：voice 后无 music 块，使用默认 vm。")

    min_ms = base_vm_ms
    max_ms = int(round(config.voice_music_intro_align_max_seconds * 1000))
    vm_final_ms, clamp_reason = _clamp_voice_music_crossfade_ms(
        candidate_ms=vm_candidate_ms,
        voice_len_ms=len(voice_audio),
        music_len_ms=len(music_audio),
        min_ms=min_ms,
        max_ms=max_ms,
    )
    logger.debug(
        "v4.3 vm 限幅: candidate=%d final=%d min=%d max=%d voice_len=%d music_len=%d reason=%s",
        vm_candidate_ms,
        vm_final_ms,
        min_ms,
        max_ms,
        len(voice_audio),
        len(music_audio),
        clamp_reason,
    )
    return vm_final_ms


def _clamp_voice_music_crossfade_ms(
    *,
    candidate_ms: int,
    voice_len_ms: int,
    music_len_ms: int,
    min_ms: int,
    max_ms: int,
) -> tuple[int, str]:
    """
    v4.3 统一限幅：候选值 -> 配置 min/max -> voice/music 时长上限。

    保证输出：0 <= vm <= min(voice_len_ms, music_len_ms)。
    当可行上限小于 min_ms（例如极短串词）时，允许降到可行上限。
    """
    feasible_cap = max(0, min(voice_len_ms, music_len_ms))
    if feasible_cap == 0:
        return 0, "cap_by_zero_length"

    lo = max(0, min_ms)
    hi = max(0, max_ms)
    if hi > 0 and lo > hi:
        lo, hi = hi, lo
        logger.debug("v4.3 vm 配置 min/max 交换: min=%d max=%d", lo, hi)

    v = max(0, candidate_ms)
    if hi > 0:
        v = min(v, hi)
    if v < lo:
        v = lo
        reason = "raise_to_min"
    else:
        reason = "keep_candidate"

    if v > feasible_cap:
        v = feasible_cap
        reason = "cap_by_voice_or_music_duration"

    return v, reason


def _concat_ordered_blocks(
    blocks: list[_TimelineBlock],
    *,
    config: AudioRenderConfig,
    voice_to_music_vm_ms_override_by_voice_segment_id: dict[str, int] | None = None,
) -> AudioSegment:
    """按相邻块类型选择转场：歌→串词硬切；串词→歌 vm；歌→歌 crossfade_seconds；串词→串词硬拼。"""
    if not blocks:
        return AudioSegment.silent(duration=0)
    acc_block = blocks[0]
    acc = acc_block.audio
    for cur_block in blocks[1:]:
        if acc_block.kind == "music" and cur_block.kind == "voice":
            acc = _join_music_to_voice_hard(acc, cur_block.audio)
            acc_block = _TimelineBlock(
                kind="voice",
                audio=acc,
                next_music_first_track_path=cur_block.next_music_first_track_path,
                voice_segment_id=cur_block.voice_segment_id,
            )
        elif acc_block.kind == "voice" and cur_block.kind == "music":
            if voice_to_music_vm_ms_override_by_voice_segment_id is not None:
                vid = acc_block.voice_segment_id
                if vid is None:
                    raise ValueError("voice->music 边界缺失 voice_segment_id，无法从阶段三覆盖参数渲染。")
                if vid not in voice_to_music_vm_ms_override_by_voice_segment_id:
                    raise ValueError(f"阶段三 transitions 缺少 voice_segment_id={vid} 的覆盖参数。")
                vm_ms = voice_to_music_vm_ms_override_by_voice_segment_id[vid]
                _validate_voice_music_vm_ms_override(
                    vm_ms=vm_ms,
                    voice_len_ms=len(acc),
                    music_len_ms=len(cur_block.audio),
                    config=config,
                )
            else:
                vm_ms = _resolve_voice_music_vm_ms(
                    acc,
                    cur_block.audio,
                    config=config,
                    next_music_first_track_path=acc_block.next_music_first_track_path or cur_block.first_track_path,
                )
            acc = _join_voice_to_music_fade_music_only(acc, cur_block.audio, vm_ms)
            acc_block = _TimelineBlock(kind="music", audio=acc, first_track_path=cur_block.first_track_path)
        elif acc_block.kind == "music" and cur_block.kind == "music":
            # v4.2 不改变歌->歌语义：仍仅使用 crossfade_seconds。
            acc = crossfade_concat([acc, cur_block.audio], crossfade_seconds=config.crossfade_seconds)
            acc_block = _TimelineBlock(kind="music", audio=acc, first_track_path=acc_block.first_track_path)
        elif acc_block.kind == "voice" and cur_block.kind == "voice":
            acc = acc + cur_block.audio
            acc_block = _TimelineBlock(
                kind="voice",
                audio=acc,
                next_music_first_track_path=cur_block.next_music_first_track_path,
                voice_segment_id=cur_block.voice_segment_id,
            )
        else:
            raise RuntimeError(f"未预期的块相邻: {acc_block.kind} -> {cur_block.kind}")
    return acc


def _validate_voice_music_vm_ms_override(
    *,
    vm_ms: int,
    voice_len_ms: int,
    music_len_ms: int,
    config: AudioRenderConfig,
) -> None:
    """
    阶段三对用户覆盖 vm 的“硬校验”：
    - 保证 0 <= vm <= min(voice_len, music_len)
    - 保证满足配置 min/max（在可行上限小于 min 时放宽到可行上限，保持与 v4.3 语义一致）
    """
    if not isinstance(vm_ms, int) or isinstance(vm_ms, bool):
        raise ValueError(f"vm_ms 必须是 int，收到: {type(vm_ms).__name__}")
    if vm_ms < 0:
        raise ValueError(f"vm_ms 不能为负：{vm_ms}")
    feasible_cap = max(0, min(voice_len_ms, music_len_ms))
    if vm_ms > feasible_cap:
        raise ValueError(f"vm_ms 超出可行上限：vm_ms={vm_ms} feasible_cap={feasible_cap}")

    min_ms = int(round(config.voice_music_crossfade_seconds * 1000))
    max_ms = int(round(config.voice_music_intro_align_max_seconds * 1000))

    # 可行上限小于 min 时，允许 vm 下降到可行上限（v4.3 语义）。
    if feasible_cap < min_ms:
        return

    if vm_ms < min_ms:
        raise ValueError(f"vm_ms 小于配置最小值：vm_ms={vm_ms} min_ms={min_ms}")
    if max_ms > 0 and vm_ms > max_ms:
        raise ValueError(f"vm_ms 大于配置最大值：vm_ms={vm_ms} max_ms={max_ms}")


def _compute_voice_music_vm_meta_by_lengths(
    *,
    voice_len_ms: int,
    music_len_ms: int,
    config: AudioRenderConfig,
    next_music_first_track_path: Path | None,
) -> tuple[int, int, float | None, float, str]:
    """
    阶段二：对 voice->music 边界计算候选 vm 与最终 vm，并返回 intro 估计信息。

    返回：
      (vm_candidate_ms, vm_final_ms, intro_seconds, confidence, intro_or_reason)
    """
    base_vm_ms = int(round(config.voice_music_crossfade_seconds * 1000))
    if base_vm_ms <= 0:
        return 0, 0, None, 0.0, "base_vm_disabled"

    vm_candidate_ms = base_vm_ms
    intro_seconds: float | None = None
    confidence: float = 0.0
    intro_reason = "intro_align_skipped"

    if config.voice_music_intro_align_enabled and next_music_first_track_path is not None:
        estimate = estimate_track_intro_seconds(
            next_music_first_track_path,
            max_intro_seconds=config.voice_music_intro_align_max_seconds,
        )
        if estimate.intro_seconds is not None:
            intro_seconds = estimate.intro_seconds
            confidence = estimate.confidence
            intro_reason = estimate.reason

            intro_ms = int(round(estimate.intro_seconds * 1000))
            cap_ms = int(round(config.voice_music_intro_align_max_seconds * 1000))
            if cap_ms > 0:
                intro_ms = min(intro_ms, cap_ms)
            if intro_ms > 0:
                # 保持 v3.9.1：base_vm 作为下限，intro/cap 仅用于放大上界。
                vm_candidate_ms = max(base_vm_ms, intro_ms)
            else:
                # intro 太小，回退默认
                vm_candidate_ms = base_vm_ms
        else:
            intro_reason = estimate.reason
    elif config.voice_music_intro_align_enabled:
        intro_reason = "voice 后无 music 块"

    min_ms = base_vm_ms
    max_ms = int(round(config.voice_music_intro_align_max_seconds * 1000))
    vm_final_ms, clamp_reason = _clamp_voice_music_crossfade_ms(
        candidate_ms=vm_candidate_ms,
        voice_len_ms=voice_len_ms,
        music_len_ms=music_len_ms,
        min_ms=min_ms,
        max_ms=max_ms,
    )
    if clamp_reason and clamp_reason != "keep_candidate":
        intro_reason = f"{intro_reason}|{clamp_reason}"
    return vm_candidate_ms, vm_final_ms, intro_seconds, confidence, intro_reason


class Mixer:
    """v3.9.1：有串词时按时间线块序列混音；无串词时整轨歌曲 crossfade。"""

    def build_mix(
        self,
        selected_tracks: list[SelectedTrack],
        voiceovers: list[VoiceoverSegment],
        config: AudioRenderConfig,
        output_path: Path,
    ) -> MixRenderSummary:
        """
        执行混音并输出中间文件。

        目标状态机（有串词时）：块类型仅为 music | voice，时间顺序严格递增；相邻块转场为
        - music → voice：硬切（`_join_music_to_voice_hard`）
        - voice → music：`_join_voice_to_music_fade_music_only`（`voice_music_crossfade_seconds`）
        - music → music：`crossfade_seconds`（歌曲-歌曲叠化）

        与 `pipeline.create_episode` 一致：`plan` 不参与混音；`VoiceoverSegment.insert_time_in_episode`
        与 `SelectedTrack` 的 start/end 锚点对齐（由 snapshot 与 TTS 管线写入）。

        无串词：全部曲目一次 `crossfade_concat`（与历史行为兼容）。
        """
        cf = config.crossfade_seconds
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not selected_tracks:
            raise ValueError("selected_tracks 不能为空。")

        if not voiceovers:
            track_audios = [_load_track_segment(st) for st in selected_tracks]
            mix = crossfade_concat(track_audios, cf)
        else:
            blocks = _build_ordered_blocks_from_tracks_and_voiceovers(
                selected_tracks,
                voiceovers,
                crossfade_seconds=cf,
                eps=_TIMELINE_EPS_SECONDS,
            )
            mix = _concat_ordered_blocks(
                blocks,
                config=config,
            )

        export_audio(mix, output_path, format=output_path.suffix.lstrip(".") or "wav")

        duration_sec = len(mix) / 1000.0
        logger.info(
            "混音完成: %s，时长 %.1fs，曲目 %d 首，主持 %d 段",
            output_path,
            duration_sec,
            len(selected_tracks),
            len(voiceovers),
        )
        return MixRenderSummary(
            mix_path=output_path,
            actual_duration_seconds=duration_sec,
            track_count=len(selected_tracks),
            voiceover_count=len(voiceovers),
        )

    def build_mix_params(
        self,
        selected_tracks: list[SelectedTrack],
        voiceovers: list[VoiceoverSegment],
        config: AudioRenderConfig,
    ) -> list[MixParamsTransition]:
        """
        v4.5：阶段二——仅生成 voice->music 边界的可编辑转场参数，不落盘最终音频。

        说明：
        - voice->music 的 vm 参数由现有 v4.2/v4.3 算法决定（librosa intro 估计 + clamp）。
        - 渲染语义与 `build_mix` 保持一致：同一时间线状态机、同一对 voice->music 边界的 vm 计算输入。
        """
        cf = config.crossfade_seconds
        if not selected_tracks:
            raise ValueError("selected_tracks 不能为空。")
        if not voiceovers:
            return []

        blocks = _build_ordered_blocks_from_tracks_and_voiceovers(
            selected_tracks,
            voiceovers,
            crossfade_seconds=cf,
            eps=_TIMELINE_EPS_SECONDS,
        )
        if not blocks:
            return []

        transitions: list[MixParamsTransition] = []

        # 只维护长度与状态机，不做实际音频拼接（阶段二不负责渲染）。
        acc_kind: BlockKind = blocks[0].kind
        acc_len_ms = len(blocks[0].audio)
        acc_first_track_path = blocks[0].first_track_path
        acc_next_music_first_track_path = blocks[0].next_music_first_track_path
        acc_voice_segment_id = blocks[0].voice_segment_id

        cf_ms = int(round(cf * 1000))

        for cur_block in blocks[1:]:
            cur_kind = cur_block.kind
            cur_len_ms = len(cur_block.audio)

            if acc_kind == "music" and cur_kind == "voice":
                # music -> voice：硬切拼接，语义为 “acc + voice”，长度相加
                acc_kind = "voice"
                acc_len_ms = acc_len_ms + cur_len_ms
                acc_voice_segment_id = cur_block.voice_segment_id
                acc_next_music_first_track_path = cur_block.next_music_first_track_path
                acc_first_track_path = None
            elif acc_kind == "voice" and cur_kind == "music":
                # voice -> music：生成可编辑 vm
                if acc_voice_segment_id is None:
                    raise ValueError("stage2: voice->music 边界缺失 voice_segment_id。")
                next_path = acc_next_music_first_track_path or cur_block.first_track_path
                if next_path is None:
                    raise ValueError("stage2: voice->music 边界缺失 next_music_first_track_path。")

                vm_candidate_ms, vm_final_ms, intro_seconds, confidence, reason = (
                    _compute_voice_music_vm_meta_by_lengths(
                        voice_len_ms=acc_len_ms,
                        music_len_ms=cur_len_ms,
                        config=config,
                        next_music_first_track_path=next_path,
                    )
                )

                transitions.append(
                    MixParamsTransition(
                        voice_segment_id=acc_voice_segment_id,
                        next_music_first_track_file_path=next_path,
                        intro_seconds=intro_seconds,
                        confidence=confidence,
                        reason=reason,
                        vm_candidate_seconds=vm_candidate_ms / 1000.0,
                        vm_seconds=vm_final_ms / 1000.0,
                    )
                )

                # voice -> music：len = voice_len + music_len - vm
                acc_len_ms = acc_len_ms + cur_len_ms - vm_final_ms
                acc_kind = "music"
                acc_first_track_path = cur_block.first_track_path
                acc_next_music_first_track_path = None
                acc_voice_segment_id = None
            elif acc_kind == "music" and cur_kind == "music":
                # music -> music：crossfade overlap = min(cf_ms, lenA, lenB)
                overlap_ms = min(cf_ms, acc_len_ms, cur_len_ms) if cf_ms > 0 else 0
                acc_len_ms = acc_len_ms + cur_len_ms - overlap_ms
                acc_kind = "music"
                # first_track_path 继承 acc_block 语义（与 build_mix 保持一致）
                acc_first_track_path = acc_first_track_path
                acc_next_music_first_track_path = None
            elif acc_kind == "voice" and cur_kind == "voice":
                # voice -> voice：硬拼接，长度相加；边界转场使用最后一条 voice_segment_id
                acc_len_ms = acc_len_ms + cur_len_ms
                acc_voice_segment_id = cur_block.voice_segment_id
                acc_next_music_first_track_path = cur_block.next_music_first_track_path
            else:
                raise RuntimeError(f"stage2 未预期的块相邻: {acc_kind} -> {cur_kind}")

        return transitions

    def render_final_mix_from_mix_params(
        self,
        mix_params_json: MixParamsJSON,
        config: AudioRenderConfig,
        output_path: Path,
    ) -> MixRenderSummary:
        """
        v4.5：阶段三——基于用户编辑后的 MixParamsJSON 渲染最终混音。

        严格约束：
        - 必须存在 transitions 对应的每个 voice->music 边界；
        - next_music_first_track_file_path 必须与当前 tracks/voiceovers 时间线一致；
        - vm_seconds 必须满足 v4.3 合法重叠区间；非法值直接报错中止。
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not mix_params_json.tracks:
            raise ValueError("mix_params_json.tracks 不能为空。")
        if not mix_params_json.voiceovers:
            # 没有串词：直接走旧语义
            track_audios = [_load_track_segment(st) for st in mix_params_json.tracks]
            mix = crossfade_concat(track_audios, config.crossfade_seconds)
            export_audio(mix, output_path, format=output_path.suffix.lstrip(".") or "wav")
            duration_sec = len(mix) / 1000.0
            return MixRenderSummary(
                mix_path=output_path,
                actual_duration_seconds=duration_sec,
                track_count=len(mix_params_json.tracks),
                voiceover_count=len(mix_params_json.voiceovers),
            )

        blocks = _build_ordered_blocks_from_tracks_and_voiceovers(
            mix_params_json.tracks,
            mix_params_json.voiceovers,
            crossfade_seconds=config.crossfade_seconds,
            eps=_TIMELINE_EPS_SECONDS,
        )

        transitions_by_voice_segment_id: dict[str, MixParamsTransition] = {
            t.voice_segment_id: t for t in mix_params_json.transitions
        }
        used_transition_ids: set[str] = set()
        override_map: dict[str, int] = {}

        # 先做边界映射与 vm 值合法性校验（不实际渲染）
        acc_kind: BlockKind = blocks[0].kind
        acc_len_ms = len(blocks[0].audio)
        acc_next_music_first_track_path = blocks[0].next_music_first_track_path
        acc_voice_segment_id = blocks[0].voice_segment_id

        cf_ms = int(round(config.crossfade_seconds * 1000))

        for cur_block in blocks[1:]:
            cur_kind = cur_block.kind
            cur_len_ms = len(cur_block.audio)

            if acc_kind == "music" and cur_kind == "voice":
                acc_kind = "voice"
                acc_len_ms = acc_len_ms + cur_len_ms
                acc_voice_segment_id = cur_block.voice_segment_id
                acc_next_music_first_track_path = cur_block.next_music_first_track_path
            elif acc_kind == "voice" and cur_kind == "music":
                if acc_voice_segment_id is None:
                    raise ValueError("stage3: voice->music 边界缺失 voice_segment_id。")
                next_path = acc_next_music_first_track_path or cur_block.first_track_path
                if next_path is None:
                    raise ValueError("stage3: voice->music 边界缺失 next_music_first_track_path。")

                if acc_voice_segment_id not in transitions_by_voice_segment_id:
                    raise ValueError(
                        f"stage3: 缺少 voice_segment_id={acc_voice_segment_id} 的 transitions 覆盖条目。"
                    )
                tr = transitions_by_voice_segment_id[acc_voice_segment_id]

                if Path(tr.next_music_first_track_file_path) != Path(next_path):
                    raise ValueError(
                        "stage3: transitions 与当前 tracks/voiceovers 边界映射不一致："
                        f"voice_segment_id={acc_voice_segment_id} "
                        f"json_next_music_first_track_file_path={tr.next_music_first_track_file_path} "
                        f"actual_next_music_first_track_file_path={next_path}"
                    )

                if not math.isfinite(tr.vm_seconds) or tr.vm_seconds < 0:
                    raise ValueError(f"stage3: 非法 vm_seconds：voice_segment_id={acc_voice_segment_id} vm={tr.vm_seconds}")
                vm_override_ms = int(round(tr.vm_seconds * 1000))
                _validate_voice_music_vm_ms_override(
                    vm_ms=vm_override_ms,
                    voice_len_ms=acc_len_ms,
                    music_len_ms=cur_len_ms,
                    config=config,
                )

                used_transition_ids.add(acc_voice_segment_id)
                override_map[acc_voice_segment_id] = vm_override_ms

                acc_len_ms = acc_len_ms + cur_len_ms - vm_override_ms
                acc_kind = "music"
                acc_voice_segment_id = None
                acc_next_music_first_track_path = None
            elif acc_kind == "music" and cur_kind == "music":
                overlap_ms = min(cf_ms, acc_len_ms, cur_len_ms) if cf_ms > 0 else 0
                acc_len_ms = acc_len_ms + cur_len_ms - overlap_ms
                acc_kind = "music"
            elif acc_kind == "voice" and cur_kind == "voice":
                acc_len_ms = acc_len_ms + cur_len_ms
                acc_voice_segment_id = cur_block.voice_segment_id
                acc_next_music_first_track_path = cur_block.next_music_first_track_path
            else:
                raise RuntimeError(f"stage3 未预期的块相邻: {acc_kind} -> {cur_kind}")

        all_ids = set(transitions_by_voice_segment_id.keys())
        if used_transition_ids != all_ids:
            missing = all_ids - used_transition_ids
            extra = used_transition_ids - all_ids
            raise ValueError(
                "stage3 transitions 条目数量/边界映射不匹配："
                f"missing={sorted(missing)} extra={sorted(extra)}"
            )

        mix = _concat_ordered_blocks(
            blocks,
            config=config,
            voice_to_music_vm_ms_override_by_voice_segment_id=override_map,
        )
        export_audio(mix, output_path, format=output_path.suffix.lstrip(".") or "wav")
        duration_sec = len(mix) / 1000.0
        return MixRenderSummary(
            mix_path=output_path,
            actual_duration_seconds=duration_sec,
            track_count=len(mix_params_json.tracks),
            voiceover_count=len(mix_params_json.voiceovers),
        )

"""v7.3：Script Writer 整期扁平歌单索引 + script_before ↔ segments[*].script 映射。

锚点一律用 episode_track_index（下标），不用曲名回填。
跨段缝统一落到「后一首所属位置」的 before（intro / between），不双槽各写一句。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from podcast_ai.core.exceptions import AIServiceError

# Critic location → episode_track_index（revision 保持未点名 cues）
_RE_SEGMENT_INTRO = re.compile(
    r"segments\s*\[\s*(?P<si>\d+)\s*\]\s*\.?\s*script\s*\.?\s*segment_intro",
    re.IGNORECASE,
)
_RE_BETWEEN = re.compile(
    r"segments\s*\[\s*(?P<si>\d+)\s*\]\s*\.?\s*script\s*\.?\s*between_tracks"
    r"(?:\s*\[\s*(?P<bi>\d+)\s*\])?",
    re.IGNORECASE,
)
_RE_AFTER_IDX = re.compile(r"after_track_index\s*[=:]\s*(?P<ai>\d+)", re.IGNORECASE)
_RE_SCRIPT_BEFORE = re.compile(r"script_before\s*\[\s*(?P<i>\d+)\s*\]", re.IGNORECASE)
_RE_EPISODE_IDX = re.compile(r"episode_track_index\s*[=:]\s*(?P<i>\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class FlatTrack:
    episode_track_index: int
    segment_index: int
    track_index_in_segment: int
    segment_id: str
    track: str
    artist: str
    selection_reason: str = ""


def build_flat_tracks(segments: Sequence[Any]) -> list[FlatTrack]:
    """按段顺序拼接 playlist，生成带 episode_track_index 的扁平表。"""
    out: list[FlatTrack] = []
    epi = 0
    for si, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            continue
        seg_id = str(seg.get("segment_id") or f"seg_{si + 1:02d}")
        playlist = seg.get("playlist") or []
        if not isinstance(playlist, list):
            raise AIServiceError(f"segments[{si}].playlist 必须是数组。")
        for ti, item in enumerate(playlist):
            if not isinstance(item, dict):
                raise AIServiceError(f"segments[{si}].playlist[{ti}] 必须是对象。")
            out.append(
                FlatTrack(
                    episode_track_index=epi,
                    segment_index=si,
                    track_index_in_segment=ti,
                    segment_id=seg_id,
                    track=str(item.get("track") or ""),
                    artist=str(item.get("artist") or ""),
                    selection_reason=str(item.get("selection_reason") or ""),
                )
            )
            epi += 1
    return out


def total_track_count(segments: Sequence[Any]) -> int:
    return len(build_flat_tracks(segments))


def local_to_episode_index(flat: Sequence[FlatTrack], *, segment_index: int, track_index: int) -> int:
    for t in flat:
        if t.segment_index == segment_index and t.track_index_in_segment == track_index:
            return t.episode_track_index
    raise AIServiceError(
        f"找不到 (segment_index={segment_index}, track_index={track_index}) 对应的 episode_track_index。"
    )


def episode_to_local(flat: Sequence[FlatTrack], episode_track_index: int) -> FlatTrack:
    if episode_track_index < 0 or episode_track_index >= len(flat):
        raise AIServiceError(
            f"episode_track_index 越界：{episode_track_index}（N={len(flat)}）。"
        )
    return flat[episode_track_index]


def format_flat_playlist_lines(flat: Sequence[FlatTrack]) -> str:
    """Adapter §4 展示格式：`[i] Artist – Track (seg_id)`。"""
    lines: list[str] = []
    for t in flat:
        artist = t.artist or "?"
        track = t.track or "?"
        lines.append(f"[{t.episode_track_index}] {artist} – {track}   ({t.segment_id})")
    return "\n".join(lines)


def build_writer_planning_brief(state: dict[str, Any]) -> str:
    """压缩策划要点，避免整份 PlanState 倾倒。"""
    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    gc = state.get("global_constraints") if isinstance(state.get("global_constraints"), dict) else {}
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    parts = [
        f"language: {meta.get('language') or ''}",
        f"theme: {meta.get('theme') or ''}",
        f"theme_description: {meta.get('theme_description') or ''}",
        f"theme_type: {meta.get('theme_type') or ''}",
        f"theme_subject: {meta.get('theme_subject') or ''}",
        f"theme_relationship: {meta.get('theme_relationship') or ''}",
        f"energy_strategy: {gc.get('energy_strategy') or ''}",
        f"sonic_world: {gc.get('sonic_world') or []}",
        f"avoid: {gc.get('avoid') or []}",
        f"episode_direction: {plan.get('episode_direction') or ''}",
        f"segments_design: {plan.get('segments_design') or ''}",
        "segments:",
    ]
    for si, seg in enumerate(state.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        parts.append(
            f"  - [{si}] id={seg.get('segment_id')} name={seg.get('name')!r} "
            f"narrative={seg.get('narrative_function')!r} scene={seg.get('scene')!r}"
        )
    return "\n".join(parts)


def normalize_cue_text(value: Any) -> str | None:
    """空串 → null；非空 str 原样；其它类型由调用方校验。"""
    if value is None:
        return None
    if isinstance(value, str):
        return None if value.strip() == "" else value
    return value  # type: ignore[return-value]


def flatten_script_before_from_state(state: dict[str, Any]) -> list[str | None]:
    """由已落盘的 segments[*].script 反推定长 script_before（revision 基线）。"""
    segments = state.get("segments") or []
    flat = build_flat_tracks(segments)
    cues: list[str | None] = [None] * len(flat)
    for t in flat:
        seg = segments[t.segment_index]
        script = seg.get("script") if isinstance(seg, dict) else None
        if not isinstance(script, dict):
            continue
        if t.track_index_in_segment == 0:
            cues[t.episode_track_index] = normalize_cue_text(script.get("segment_intro"))
            continue
        between = script.get("between_tracks") or []
        want_after = t.track_index_in_segment - 1
        if isinstance(between, list):
            for bt in between:
                if not isinstance(bt, dict):
                    continue
                if bt.get("after_track_index") == want_after:
                    cues[t.episode_track_index] = normalize_cue_text(bt.get("text"))
                    break
    return cues


def map_script_before_to_segments_patch(
    script_before: Sequence[Any],
    segments: Sequence[Any],
) -> dict[str, Any]:
    """
    定长 script_before → 可 merge 的 ``{"segments":[{"script":...}, ...]}``。

    - 段内第 0 首 before → segment_intro
    - 段内第 k≥1 首 before → between_tracks {after_track_index: k-1, text}
    - 每段补齐 between 槽 0…L-2；无文案 text=null
    """
    flat = build_flat_tracks(segments)
    n = len(flat)
    if len(script_before) != n:
        raise AIServiceError(
            f"script_before 长度必须 = N={n}（收到 {len(script_before)}）；禁止写入半套 script。"
        )

    # segment_index → {intro, between_by_after}
    intros: dict[int, str | None] = {}
    betweens: dict[int, dict[int, str | None]] = {}
    lengths: dict[int, int] = {}

    for si, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            raise AIServiceError(f"segments[{si}] 必须是对象。")
        pl = seg.get("playlist") or []
        if not isinstance(pl, list):
            raise AIServiceError(f"segments[{si}].playlist 必须是数组。")
        lengths[si] = len(pl)
        intros[si] = None
        betweens[si] = {}

    for i, raw in enumerate(script_before):
        text = normalize_cue_text(raw)
        if text is not None and not isinstance(text, str):
            raise AIServiceError(f"script_before[{i}] 必须是 string 或 null。")
        t = flat[i]
        if t.track_index_in_segment == 0:
            intros[t.segment_index] = text
        else:
            after = t.track_index_in_segment - 1
            betweens[t.segment_index][after] = text

    out_segments: list[dict[str, Any]] = []
    for si in range(len(segments or [])):
        L = lengths.get(si, 0)
        bt_list: list[dict[str, Any]] = []
        # L<=1 → 无曲间槽
        for after in range(max(0, L - 1)):
            bt_list.append(
                {
                    "after_track_index": after,
                    "text": betweens.get(si, {}).get(after),
                }
            )
        out_segments.append(
            {
                "script": {
                    "segment_intro": intros.get(si),
                    "between_tracks": bt_list,
                }
            }
        )
    return {"segments": out_segments}


def parse_action_episode_indices(
    actions: Sequence[Any],
    flat: Sequence[FlatTrack],
) -> set[int]:
    """从 Critic actions.location 解析可变 episode_track_index 集合。"""
    touched: set[int] = set()
    for act in actions or []:
        if not isinstance(act, dict):
            continue
        loc = str(act.get("location") or "")
        if not loc.strip():
            continue
        for m in _RE_SCRIPT_BEFORE.finditer(loc):
            touched.add(int(m.group("i")))
        for m in _RE_EPISODE_IDX.finditer(loc):
            touched.add(int(m.group("i")))
        m_intro = _RE_SEGMENT_INTRO.search(loc)
        if m_intro:
            si = int(m_intro.group("si"))
            try:
                touched.add(local_to_episode_index(flat, segment_index=si, track_index=0))
            except AIServiceError:
                pass
            continue
        m_bt = _RE_BETWEEN.search(loc)
        if m_bt:
            si = int(m_bt.group("si"))
            after: int | None = None
            m_after = _RE_AFTER_IDX.search(loc)
            if m_after:
                after = int(m_after.group("ai"))
            elif m_bt.group("bi") is not None:
                # between_tracks[bi]：若与 after 对齐不确定，尽量用 bi 当 after
                after = int(m_bt.group("bi"))
            if after is not None:
                try:
                    touched.add(
                        local_to_episode_index(flat, segment_index=si, track_index=after + 1)
                    )
                except AIServiceError:
                    pass
    return touched


def apply_revision_preserve(
    new_cues: Sequence[Any],
    previous_cues: Sequence[str | None],
    mutable_indices: set[int],
) -> list[str | None]:
    """
    revision：未点名下标强制与上一轮落盘一致（null/空白已规范化）。
    若 mutable_indices 为空（无法解析 location），不强制覆盖，避免误伤。
    """
    if len(new_cues) != len(previous_cues):
        raise AIServiceError(
            f"revision script_before 长度须与上一轮一致：prev={len(previous_cues)} new={len(new_cues)}。"
        )
    if not mutable_indices:
        return [normalize_cue_text(x) if isinstance(x, (str, type(None))) else x for x in new_cues]  # type: ignore[misc]

    out: list[str | None] = []
    for i, raw in enumerate(new_cues):
        if i in mutable_indices:
            text = normalize_cue_text(raw)
            if text is not None and not isinstance(text, str):
                raise AIServiceError(f"script_before[{i}] 必须是 string 或 null。")
            out.append(text)
        else:
            out.append(previous_cues[i])
    return out


def translate_locations_for_prompt(
    actions: Sequence[Any],
    flat: Sequence[FlatTrack],
) -> list[dict[str, Any]]:
    """供 prompt 展示：附带 episode_track_index 译注。"""
    out: list[dict[str, Any]] = []
    for act in actions or []:
        if not isinstance(act, dict):
            continue
        item = dict(act)
        idxs = sorted(parse_action_episode_indices([act], flat))
        if idxs:
            item["episode_track_index_hint"] = idxs
        out.append(item)
    return out

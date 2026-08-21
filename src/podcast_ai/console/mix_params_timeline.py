"""v5.3：MixParamsJSON ⇄ 节目时间线视图（纯函数适配器，无 UI）。

时间线规则对齐 Mixer 块序：按曲目 end 与串词 insert_time 交错；
仅在 voice→music 边界插入 transition（按 voice_segment_id 关联）。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from podcast_ai.core.models import MixParamsJSON, MixParamsTransition

MixTimelineItemKind = Literal["music", "voice", "transition"]

# 与 mixer 锚点判断同量级
_TIMELINE_EPS_SECONDS = 0.05


@dataclass
class MixTimelineItem:
    """节目时间线节点：music / voice / transition。"""

    kind: MixTimelineItemKind
    # 稳定 id，供 UI 选中与 apply_vm_edits 定位（transition 用 voice_segment_id）
    node_id: str

    # music
    track_id: str | None = None
    title: str | None = None
    artist: str | None = None
    audio_path: Path | None = None
    start_time_in_episode: float | None = None
    end_time_in_episode: float | None = None

    # voice
    voice_segment_id: str | None = None
    text: str | None = None
    insert_time_in_episode: float | None = None

    # transition（voice→music）；本轮可编辑字段仅 vm_seconds
    vm_seconds: float | None = None
    vm_candidate_seconds: float | None = None
    intro_seconds: float | None = None
    confidence: float | None = None
    reason: str | None = None
    next_music_first_track_file_path: Path | None = None


@dataclass
class MixTimelineDocument:
    """可读节目时间线；meta 等原样保留在 source 中供写回。"""

    items: list[MixTimelineItem] = field(default_factory=list)
    # 完整 MixParams 快照（dict），apply 时在其基础上只改 transitions.vm_seconds
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for it in self.items:
            row = asdict(it)
            for key, val in list(row.items()):
                if isinstance(val, Path):
                    row[key] = str(val)
            items.append(row)
        return {"items": items, "source": self.source}


def mix_params_to_timeline(mix_params: MixParamsJSON | dict[str, Any]) -> MixTimelineDocument:
    """
    将 MixParamsJSON 展开为节目时间线：

    … → music* → voice → transition → music* → …
    """
    model = (
        mix_params
        if isinstance(mix_params, MixParamsJSON)
        else MixParamsJSON.model_validate(mix_params)
    )
    if not model.tracks:
        raise ValueError("MixParamsJSON.tracks 不能为空。")

    tracks_sorted = sorted(model.tracks, key=lambda st: float(st.start_time_in_episode))
    voiceovers_sorted = sorted(
        model.voiceovers,
        key=lambda vo: (float(vo.insert_time_in_episode), vo.segment_id),
    )
    transitions_by_vid = {t.voice_segment_id: t for t in model.transitions}
    used_transition_ids: set[str] = set()

    items: list[MixTimelineItem] = []
    track_idx = 0
    n = len(tracks_sorted)

    def _emit_music(st_index: int) -> None:
        st = tracks_sorted[st_index]
        path = Path(st.track.file_path)
        if not str(st.track.file_path).strip():
            raise ValueError(f"曲目路径缺失：track_id={st.track.id}")
        items.append(
            MixTimelineItem(
                kind="music",
                node_id=f"music:{st.track.id}:{st_index}",
                track_id=st.track.id,
                title=st.track.title,
                artist=st.track.artist,
                audio_path=path,
                start_time_in_episode=float(st.start_time_in_episode),
                end_time_in_episode=float(st.end_time_in_episode),
            )
        )

    for vo in voiceovers_sorted:
        insert_t = float(vo.insert_time_in_episode)
        while track_idx < n and float(tracks_sorted[track_idx].end_time_in_episode) <= insert_t + _TIMELINE_EPS_SECONDS:
            _emit_music(track_idx)
            track_idx += 1

        if not str(vo.audio_path).strip():
            raise ValueError(f"串词音频路径缺失：voice_segment_id={vo.segment_id}")
        items.append(
            MixTimelineItem(
                kind="voice",
                node_id=f"voice:{vo.segment_id}",
                voice_segment_id=vo.segment_id,
                text=vo.text,
                audio_path=Path(vo.audio_path),
                insert_time_in_episode=insert_t,
            )
        )

        # voice 后仍有 music → 必须存在对应 transition
        if track_idx < n:
            tr = transitions_by_vid.get(vo.segment_id)
            if tr is None:
                raise ValueError(
                    f"缺失 voice→music 转场：voice_segment_id={vo.segment_id} "
                    f"（其后曲目 track_id={tracks_sorted[track_idx].track.id}）"
                )
            used_transition_ids.add(vo.segment_id)
            items.append(
                MixTimelineItem(
                    kind="transition",
                    node_id=f"transition:{vo.segment_id}",
                    voice_segment_id=vo.segment_id,
                    vm_seconds=float(tr.vm_seconds),
                    vm_candidate_seconds=float(tr.vm_candidate_seconds),
                    intro_seconds=tr.intro_seconds,
                    confidence=float(tr.confidence),
                    reason=tr.reason or "",
                    next_music_first_track_file_path=Path(tr.next_music_first_track_file_path),
                )
            )

    while track_idx < n:
        _emit_music(track_idx)
        track_idx += 1

    unused = set(transitions_by_vid.keys()) - used_transition_ids
    if unused:
        raise ValueError(
            "transitions 未映射到任何 voice→music 边界："
            f"{sorted(unused)}"
        )

    return MixTimelineDocument(
        items=items,
        source=json.loads(model.model_dump_json()),
    )


def apply_vm_edits(
    mix_params: MixParamsJSON | dict[str, Any],
    edits: dict[str, float],
) -> MixParamsJSON:
    """
    仅覆盖 transitions[*].vm_seconds（key = voice_segment_id）；其余字段透传。

    - 未知 voice_segment_id / 负值 / NaN：明确报错
    - 返回值经 MixParamsJSON.model_validate
    """
    model = (
        mix_params
        if isinstance(mix_params, MixParamsJSON)
        else MixParamsJSON.model_validate(mix_params)
    )
    if not edits:
        return model

    known = {t.voice_segment_id for t in model.transitions}
    unknown = sorted(set(edits.keys()) - known)
    if unknown:
        raise ValueError(f"vm 编辑引用了不存在的 voice_segment_id：{unknown}")

    patched: list[MixParamsTransition] = []
    for tr in model.transitions:
        if tr.voice_segment_id not in edits:
            patched.append(tr)
            continue
        raw = edits[tr.voice_segment_id]
        try:
            vm = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"vm_seconds 非法（voice_segment_id={tr.voice_segment_id}）：{raw!r}"
            ) from exc
        if not math.isfinite(vm) or vm < 0:
            raise ValueError(
                f"vm_seconds 必须为非负有限数（voice_segment_id={tr.voice_segment_id}）：{vm}"
            )
        patched.append(tr.model_copy(update={"vm_seconds": vm}))

    payload = json.loads(model.model_dump_json())
    payload["transitions"] = [json.loads(t.model_dump_json()) for t in patched]
    try:
        return MixParamsJSON.model_validate(payload)
    except ValidationError:
        raise


def load_mix_params_timeline(path: Path) -> MixTimelineDocument:
    """从磁盘加载 MixParamsJSON 并展开为时间线。"""
    text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text)
    return mix_params_to_timeline(raw)


def timeline_source_as_mix_params(doc: MixTimelineDocument) -> MixParamsJSON:
    """将文档内保留的 source 还原为 MixParamsJSON（未应用未保存的编辑前）。"""
    return MixParamsJSON.model_validate(doc.source)


def _as_path(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    return Path(str(value))


def mix_timeline_from_dict(raw: dict[str, Any]) -> MixTimelineDocument:
    """从 ``MixTimelineDocument.to_dict()`` 还原（State 里 Path 可能已是 str）。"""
    items: list[MixTimelineItem] = []
    for row in raw.get("items") or []:
        if not isinstance(row, dict):
            continue
        payload = dict(row)
        payload["audio_path"] = _as_path(payload.get("audio_path"))
        payload["next_music_first_track_file_path"] = _as_path(
            payload.get("next_music_first_track_file_path")
        )
        items.append(MixTimelineItem(**payload))
    return MixTimelineDocument(items=items, source=dict(raw.get("source") or {}))


def find_mix_node(doc: MixTimelineDocument, node_id: str | None) -> MixTimelineItem | None:
    if not node_id:
        return None
    for it in doc.items:
        if it.node_id == node_id:
            return it
    return None


def format_mix_node_label(it: MixTimelineItem, *, edits: dict[str, float] | None = None) -> str:
    if it.kind == "music":
        title = it.title or it.track_id or "track"
        t0 = float(it.start_time_in_episode or 0.0)
        t1 = float(it.end_time_in_episode or 0.0)
        return f"[music] {t0:.1f}–{t1:.1f}s · {title}"
    if it.kind == "voice":
        t0 = float(it.insert_time_in_episode or 0.0)
        preview = (it.text or "").replace("\n", " ").strip()[:28]
        tail = f" · {preview}" if preview else ""
        return f"[voice] @{t0:.1f}s · {it.voice_segment_id}{tail}"
    vid = it.voice_segment_id or ""
    vm = (edits or {}).get(vid, it.vm_seconds)
    return f"[transition] vm={vm}s · {vid}"


def mix_node_choice_pairs(
    doc: MixTimelineDocument,
    *,
    edits: dict[str, float] | None = None,
) -> list[tuple[str, str]]:
    return [(format_mix_node_label(it, edits=edits), it.node_id) for it in doc.items]


def format_mix_timeline_markdown(
    doc: MixTimelineDocument,
    *,
    edits: dict[str, float] | None = None,
) -> str:
    """只读播出顺序：music / voice / transition 交错。"""
    if not doc.items:
        return "_（空时间线）_"
    lines = ["**节目时间线**（音乐 / 串词 / 转场）"]
    for i, it in enumerate(doc.items, start=1):
        if it.kind == "music":
            t0 = float(it.start_time_in_episode or 0.0)
            t1 = float(it.end_time_in_episode or 0.0)
            name = it.title or it.track_id or "track"
            artist = f" / {it.artist}" if it.artist else ""
            short = Path(it.audio_path).name if it.audio_path else "—"
            lines.append(f"{i}. **music** `{t0:.1f}–{t1:.1f}s` {name}{artist} · `{short}`")
        elif it.kind == "voice":
            t0 = float(it.insert_time_in_episode or 0.0)
            preview = (it.text or "").replace("\n", " ").strip()[:48] or "_(空)_"
            short = Path(it.audio_path).name if it.audio_path else "—"
            lines.append(
                f"{i}. **voice** `@{t0:.1f}s` `{it.voice_segment_id}` — {preview} · `{short}`"
            )
        else:
            vid = it.voice_segment_id or ""
            vm = (edits or {}).get(vid, it.vm_seconds)
            lines.append(
                f"{i}. **transition** `{vid}` · vm=`{vm}`s "
                f"(candidate `{it.vm_candidate_seconds}` · intro `{it.intro_seconds}`)"
            )
    return "\n".join(lines)


def save_mix_params_as_new_file(
    mix_params: MixParamsJSON,
    source_path: Path,
    *,
    dest_path: Path | None = None,
) -> Path:
    """
    校验后写入同目录新文件（默认不覆盖源文件）。

    默认文件名：``{stem}_edited_{UTC时间戳}.json``。
    """
    source = Path(source_path)
    MixParamsJSON.model_validate(json.loads(mix_params.model_dump_json()))
    if dest_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = source.parent / f"{source.stem}_edited_{ts}.json"
    else:
        dest = Path(dest_path)
        if dest.resolve() == source.resolve():
            raise ValueError("默认禁止覆盖源 MixParamsJSON；请指定不同的 dest_path。")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(mix_params.model_dump_json(indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest

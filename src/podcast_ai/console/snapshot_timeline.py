"""v5.2：Stage2Snapshot ⇄ 播出时间线视图（纯函数适配器，无 UI）。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from podcast_ai.core.models import Stage2Snapshot

TimelineItemKind = Literal["segment_intro", "track", "between_tracks"]


@dataclass
class TimelineItem:
    """时间线上的一条可读条目。"""

    kind: TimelineItemKind
    segment_id: str
    segment_name: str
    # intro / between
    text: str | None = None
    # track
    track: str | None = None
    artist: str | None = None
    # track 索引；between 时表示 after_track_index
    track_index: int | None = None


@dataclass
class TimelineSegmentView:
    segment_id: str
    name: str
    target_duration_seconds: int
    items: list[TimelineItem] = field(default_factory=list)


@dataclass
class TimelineDocument:
    """可读时间线文档（可编辑字段：intro/between 文本、track/artist）。"""

    schema: str
    meta: dict[str, Any]
    segments: list[TimelineSegmentView] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snapshot_to_timeline(snapshot: Stage2Snapshot | dict[str, Any]) -> TimelineDocument:
    """
    将 Stage2Snapshot 展开为播出时间线：

    每段顺序：segment_intro → (track_i → between_tracks[after_i])*
    """
    model = snapshot if isinstance(snapshot, Stage2Snapshot) else Stage2Snapshot.model_validate(snapshot)
    segments: list[TimelineSegmentView] = []
    for seg in model.segments:
        items: list[TimelineItem] = []
        items.append(
            TimelineItem(
                kind="segment_intro",
                segment_id=seg.segment_id,
                segment_name=seg.name,
                text=seg.script.segment_intro,
            )
        )
        # 按 after_track_index 对齐；不凭空插入原文没有的 between 槽位（保证往返不增字段）
        between_by_idx: dict[int, list[str | None]] = {}
        for bt in seg.script.between_tracks:
            between_by_idx.setdefault(int(bt.after_track_index), []).append(bt.text)

        emitted_between_idxs: set[int] = set()
        for i, pl in enumerate(seg.playlists):
            items.append(
                TimelineItem(
                    kind="track",
                    segment_id=seg.segment_id,
                    segment_name=seg.name,
                    track=pl.track,
                    artist=pl.artist,
                    track_index=i,
                )
            )
            for text in between_by_idx.get(i, []):
                items.append(
                    TimelineItem(
                        kind="between_tracks",
                        segment_id=seg.segment_id,
                        segment_name=seg.name,
                        text=text,
                        track_index=i,
                    )
                )
                emitted_between_idxs.add(i)

        # 孤儿 after_track_index（超出曲目数）挂在段末，避免丢字段
        for idx in sorted(between_by_idx.keys()):
            if idx in emitted_between_idxs:
                continue
            for text in between_by_idx[idx]:
                items.append(
                    TimelineItem(
                        kind="between_tracks",
                        segment_id=seg.segment_id,
                        segment_name=seg.name,
                        text=text,
                        track_index=idx,
                    )
                )
        segments.append(
            TimelineSegmentView(
                segment_id=seg.segment_id,
                name=seg.name,
                target_duration_seconds=seg.target_duration_seconds,
                items=items,
            )
        )
    return TimelineDocument(
        schema=model.schema_,
        meta=model.meta.model_dump(),
        segments=segments,
    )


def timeline_from_dict(raw: dict[str, Any]) -> TimelineDocument:
    """从 ``TimelineDocument.to_dict()`` 还原对象。"""
    return TimelineDocument(
        schema=str(raw.get("schema") or ""),
        meta=dict(raw.get("meta") or {}),
        segments=[
            TimelineSegmentView(
                segment_id=str(s.get("segment_id") or ""),
                name=str(s.get("name") or ""),
                target_duration_seconds=int(s.get("target_duration_seconds") or 0),
                items=[
                    TimelineItem(**it) if isinstance(it, dict) else it
                    for it in (s.get("items") or [])
                ],
            )
            for s in (raw.get("segments") or [])
        ],
    )


def format_timeline_markdown(doc: TimelineDocument) -> str:
    """只读播出顺序：章节串词 → 曲目 → 曲后串词。"""
    if not doc.segments:
        return "_（空时间线）_"
    lines = [f"**theme**: {doc.meta.get('theme', '')} · schema `{doc.schema}`"]
    for seg in doc.segments:
        lines.append(f"\n### {seg.segment_id} · {seg.name}（{seg.target_duration_seconds}s）")
        for it in seg.items:
            if it.kind == "segment_intro":
                preview = (it.text or "").strip() or "_(空)_"
                lines.append(f"- **章节串词** — {preview}")
            elif it.kind == "track":
                lines.append(
                    f"- **音乐 {it.track_index}** — {(it.track or '')} / {(it.artist or '')}"
                )
            else:
                preview = (it.text or "").strip() or "_(无串词)_"
                lines.append(f"- **曲后串词** (after {it.track_index}) — {preview}")
    return "\n".join(lines)


TABLE_HEADERS = [
    "segment_id",
    "segment_name",
    "kind",
    "text",
    "track",
    "artist",
    "track_index",
]


def timeline_to_table_rows(doc: TimelineDocument) -> list[list[Any]]:
    """供 Gradio Dataframe 编辑的行（与 TABLE_HEADERS 对齐）。"""
    rows: list[list[Any]] = []
    for seg in doc.segments:
        for it in seg.items:
            rows.append(
                [
                    it.segment_id,
                    it.segment_name,
                    it.kind,
                    it.text if it.text is not None else "",
                    it.track or "",
                    it.artist or "",
                    "" if it.track_index is None else it.track_index,
                ]
            )
    return rows


def _parse_track_index(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def table_rows_to_timeline(base: TimelineDocument, rows: list[list[Any]]) -> TimelineDocument:
    """用表格行覆盖各段 items；segment 元数据仍以 base 为准。"""
    items_by_seg: dict[str, list[TimelineItem]] = {s.segment_id: [] for s in base.segments}
    known = set(items_by_seg)
    for row in rows:
        if not row:
            continue
        cells = list(row) + [""] * max(0, 7 - len(row))
        sid = str(cells[0] or "").strip()
        kind = str(cells[2] or "").strip()
        if not sid:
            continue
        if sid not in known:
            raise ValueError(f"未知 segment_id：{sid}")
        if kind not in {"segment_intro", "track", "between_tracks"}:
            raise ValueError(f"无法识别 kind={kind!r}（segment={sid}）")
        text_raw = cells[3]
        if kind == "between_tracks":
            text_val: str | None = None if str(text_raw).strip() == "" else str(text_raw)
        elif kind == "segment_intro":
            text_val = "" if text_raw is None else str(text_raw)
        else:
            text_val = None
        items_by_seg[sid].append(
            TimelineItem(
                kind=kind,  # type: ignore[arg-type]
                segment_id=sid,
                segment_name=str(cells[1] or ""),
                text=text_val,
                track=str(cells[4] or "") if kind == "track" else None,
                artist=str(cells[5] or "") if kind == "track" else None,
                track_index=_parse_track_index(cells[6]),
            )
        )
    segments: list[TimelineSegmentView] = []
    for seg in base.segments:
        segments.append(
            TimelineSegmentView(
                segment_id=seg.segment_id,
                name=seg.name,
                target_duration_seconds=seg.target_duration_seconds,
                items=items_by_seg[seg.segment_id],
            )
        )
    return TimelineDocument(schema=base.schema, meta=base.meta, segments=segments)


def timeline_to_snapshot(doc: TimelineDocument | dict[str, Any]) -> Stage2Snapshot:
    """
    将时间线文档组装回 Stage2Snapshot；校验失败抛 ValidationError / ValueError。
    """
    if isinstance(doc, dict):
        doc = timeline_from_dict(doc)

    segments_payload: list[dict[str, Any]] = []
    for seg in doc.segments:
        intro = ""
        playlists: list[dict[str, str]] = []
        between: list[dict[str, Any]] = []
        for it in seg.items:
            if it.kind == "segment_intro":
                intro = it.text if it.text is not None else ""
            elif it.kind == "track":
                playlists.append(
                    {
                        "track": (it.track or "").strip(),
                        "artist": (it.artist or "").strip(),
                    }
                )
            elif it.kind == "between_tracks":
                if it.track_index is None:
                    raise ValueError(
                        f"segment={seg.segment_id} 的 between_tracks 缺少 track_index"
                    )
                between.append(
                    {
                        "after_track_index": int(it.track_index),
                        "text": it.text,
                    }
                )
        segments_payload.append(
            {
                "segment_id": seg.segment_id,
                "name": seg.name,
                "target_duration_seconds": seg.target_duration_seconds,
                "playlists": playlists,
                "script": {
                    "segment_intro": intro,
                    "between_tracks": between,
                },
            }
        )

    payload = {
        "schema": doc.schema,
        "meta": doc.meta,
        "segments": segments_payload,
    }
    try:
        return Stage2Snapshot.model_validate(payload)
    except ValidationError:
        raise


def load_snapshot_timeline(path: Path) -> TimelineDocument:
    """从磁盘加载 snapshot JSON 并转为时间线。"""
    text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text)
    return snapshot_to_timeline(raw)


def save_timeline_as_new_snapshot(
    doc: TimelineDocument | dict[str, Any],
    source_path: Path,
    *,
    dest_path: Path | None = None,
) -> Path:
    """
    校验后写入同目录新文件（默认不覆盖源文件）。

    默认文件名：``{stem}_edited_{UTC时间戳}.json``。
    非法 timeline / schema 校验失败时**不写盘**。
    """
    source = Path(source_path)
    model = timeline_to_snapshot(doc)
    if dest_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = source.parent / f"{source.stem}_edited_{ts}.json"
    else:
        dest = Path(dest_path)
        if dest.resolve() == source.resolve():
            raise ValueError("默认禁止覆盖源 snapshot；请指定不同的 dest_path。")

    # 先校验再写：model_dump_json 仅在校验成功后执行
    text = model.model_dump_json(by_alias=True, indent=2, ensure_ascii=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text + "\n", encoding="utf-8")
    return dest

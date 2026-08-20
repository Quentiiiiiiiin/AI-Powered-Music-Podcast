"""v5.2：Snapshot ⇄ 播出时间线往返与保存契约。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from podcast_ai.console.snapshot_timeline import (
    TimelineDocument,
    TimelineItem,
    TimelineSegmentView,
    save_timeline_as_new_snapshot,
    snapshot_to_timeline,
    timeline_to_snapshot,
)
from podcast_ai.core.models import Stage2Snapshot


def _sample_snapshot() -> dict:
    return {
        "schema": "v3.0",
        "meta": {
            "request_id": "req_test",
            "theme": "Late Night Chill",
            "language": "zh-CN",
            "target_duration_seconds": 3600,
        },
        "segments": [
            {
                "segment_id": "seg_01",
                "name": "开场",
                "target_duration_seconds": 600,
                "playlists": [
                    {"track": "Track A", "artist": "Artist A"},
                    {"track": "Track B", "artist": "Artist B"},
                ],
                "script": {
                    "segment_intro": "欢迎来到节目。",
                    "between_tracks": [
                        {"after_track_index": 0, "text": "下一首更暖。"},
                        {"after_track_index": 1, "text": None},
                    ],
                },
            },
            {
                "segment_id": "seg_02",
                "name": "中段",
                "target_duration_seconds": 900,
                "playlists": [{"track": "Track C", "artist": "Artist C"}],
                "script": {
                    "segment_intro": "进入中段。",
                    "between_tracks": [],
                },
            },
        ],
    }


def test_timeline_order_intro_track_between() -> None:
    doc = snapshot_to_timeline(_sample_snapshot())
    seg0 = doc.segments[0]
    kinds = [it.kind for it in seg0.items]
    assert kinds == [
        "segment_intro",
        "track",
        "between_tracks",
        "track",
        "between_tracks",
    ]
    assert seg0.items[0].text == "欢迎来到节目。"
    assert seg0.items[1].track == "Track A"
    assert seg0.items[2].text == "下一首更暖。"
    assert seg0.items[2].track_index == 0


def test_timeline_roundtrip_preserves_key_fields() -> None:
    original = Stage2Snapshot.model_validate(_sample_snapshot())
    doc = snapshot_to_timeline(original)
    # 轻改可编辑字段
    doc.segments[0].items[0].text = "欢迎来到节目（修订）。"
    doc.segments[0].items[1].artist = "Artist A+"
    restored = timeline_to_snapshot(doc)

    assert restored.schema_ == original.schema_
    assert restored.meta.model_dump() == original.meta.model_dump()
    assert len(restored.segments) == 2
    assert restored.segments[0].script.segment_intro == "欢迎来到节目（修订）。"
    assert restored.segments[0].playlists[0].artist == "Artist A+"
    assert restored.segments[0].playlists[1].track == "Track B"
    assert restored.segments[0].script.between_tracks[0].after_track_index == 0
    assert restored.segments[0].script.between_tracks[0].text == "下一首更暖。"
    assert restored.segments[0].script.between_tracks[1].text is None
    assert restored.segments[1].playlists[0].track == "Track C"


def test_table_rows_roundtrip_keeps_edits() -> None:
    from podcast_ai.console.snapshot_timeline import table_rows_to_timeline, timeline_to_table_rows

    original = Stage2Snapshot.model_validate(_sample_snapshot())
    doc = snapshot_to_timeline(original)
    rows = timeline_to_table_rows(doc)
    rows[0][3] = "表格改过的开场"
    rebuilt = table_rows_to_timeline(doc, rows)
    restored = timeline_to_snapshot(rebuilt)
    assert restored.segments[0].script.segment_intro == "表格改过的开场"
    assert restored.segments[0].script.between_tracks[1].text is None


def test_save_timeline_writes_new_file_valid_for_stage2(tmp_path: Path) -> None:
    src = tmp_path / "ep_demo.json"
    src.write_text(json.dumps(_sample_snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    doc = snapshot_to_timeline(_sample_snapshot())
    dest = save_timeline_as_new_snapshot(doc, src, dest_path=tmp_path / "ep_demo_edited.json")
    assert dest.exists()
    assert dest != src
    assert dest.name == "ep_demo_edited.json"
    # 阶段二契约：可被 Stage2Snapshot 解析
    loaded = Stage2Snapshot.model_validate_json(dest.read_text(encoding="utf-8"))
    assert loaded.segments[0].segment_id == "seg_01"


def test_invalid_timeline_save_does_not_write(tmp_path: Path) -> None:
    src = tmp_path / "ep_demo.json"
    src.write_text(json.dumps(_sample_snapshot(), ensure_ascii=False), encoding="utf-8")
    dest = tmp_path / "should_not_exist.json"
    bad = TimelineDocument(
        schema="v3.0",
        meta={"request_id": "x"},  # 缺 theme/language/target_duration_seconds
        segments=[
            TimelineSegmentView(
                segment_id="seg_01",
                name="开场",
                target_duration_seconds=60,
                items=[
                    TimelineItem(
                        kind="segment_intro",
                        segment_id="seg_01",
                        segment_name="开场",
                        text="hi",
                    )
                ],
            )
        ],
    )
    with pytest.raises(ValidationError):
        save_timeline_as_new_snapshot(bad, src, dest_path=dest)
    assert not dest.exists()


def test_refuse_overwrite_source_path(tmp_path: Path) -> None:
    src = tmp_path / "ep_demo.json"
    src.write_text(json.dumps(_sample_snapshot(), ensure_ascii=False), encoding="utf-8")
    doc = snapshot_to_timeline(_sample_snapshot())
    with pytest.raises(ValueError, match="禁止覆盖"):
        save_timeline_as_new_snapshot(doc, src, dest_path=src)

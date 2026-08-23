"""v5.3：MixParams 时间线展开、vm 编辑写回与另存。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_ai.console.mix_params_timeline import (
    apply_vm_edits,
    format_mix_timeline_markdown,
    mix_params_to_timeline,
    save_mix_params_as_new_file,
)
from podcast_ai.core.models import MixParamsJSON


def _sample_mix(tmp_path: Path) -> dict:
    music = tmp_path / "song.wav"
    voice = tmp_path / "vo.mp3"
    music.write_bytes(b"m")
    voice.write_bytes(b"v")
    return {
        "meta": {
            "schema_version": "v4.5",
            "theme": "test",
            "language": "zh",
            "target_duration_seconds": 60,
            "request_id": "req_1",
        },
        "plan_segments": [{"name": "开场", "target_duration_seconds": 60}],
        "style_description": "test",
        "tracks": [
            {
                "track": {
                    "id": "t1",
                    "file_path": str(music),
                    "title": "Song A",
                    "artist": "Art",
                },
                "start_time_in_episode": 5.0,
                "end_time_in_episode": 20.0,
                "effective_duration": 15.0,
            }
        ],
        "voiceovers": [
            {
                "segment_id": "vo1",
                "text": "欢迎",
                "audio_path": str(voice),
                "insert_time_in_episode": 0.0,
            }
        ],
        "transitions": [
            {
                "voice_segment_id": "vo1",
                "next_music_first_track_file_path": str(music),
                "intro_seconds": 1.2,
                "confidence": 0.8,
                "reason": "ok",
                "vm_candidate_seconds": 2.0,
                "vm_seconds": 2.5,
            }
        ],
    }


def test_timeline_order_voice_transition_music(tmp_path: Path) -> None:
    doc = mix_params_to_timeline(_sample_mix(tmp_path))
    kinds = [it.kind for it in doc.items]
    assert kinds == ["voice", "transition", "music"]
    md = format_mix_timeline_markdown(doc)
    assert "voice" in md and "transition" in md and "music" in md
    assert "2.5" in md


def test_apply_vm_edits_preserves_other_fields(tmp_path: Path) -> None:
    original = MixParamsJSON.model_validate(_sample_mix(tmp_path))
    patched = apply_vm_edits(original, {"vo1": 4.0})
    assert patched.transitions[0].vm_seconds == 4.0
    assert patched.transitions[0].vm_candidate_seconds == 2.0
    assert patched.transitions[0].intro_seconds == 1.2
    assert patched.tracks[0].track.title == "Song A"


def test_apply_vm_rejects_negative(tmp_path: Path) -> None:
    original = MixParamsJSON.model_validate(_sample_mix(tmp_path))
    with pytest.raises(ValueError, match="非负"):
        apply_vm_edits(original, {"vo1": -1.0})


def test_save_writes_new_file_valid_for_stage3(tmp_path: Path) -> None:
    src = tmp_path / "ep_mix_params.json"
    payload = _sample_mix(tmp_path)
    src.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    model = MixParamsJSON.model_validate(payload)
    patched = apply_vm_edits(model, {"vo1": 3.25})
    dest = save_mix_params_as_new_file(patched, src, dest_path=tmp_path / "ep_mix_params_edited.json")
    assert dest.exists()
    assert dest != src
    loaded = MixParamsJSON.model_validate_json(dest.read_text(encoding="utf-8"))
    assert loaded.transitions[0].vm_seconds == 3.25

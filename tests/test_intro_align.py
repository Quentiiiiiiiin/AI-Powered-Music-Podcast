from __future__ import annotations

from pathlib import Path

from podcast_ai.modules.mixing.intro_align import estimate_track_intro_seconds


def test_intro_align_returns_fallback_when_track_missing(tmp_path: Path) -> None:
    out = estimate_track_intro_seconds(tmp_path / "no_such_file.wav", max_intro_seconds=3.0)
    assert out.intro_seconds is None
    assert out.reason == "track_not_found"


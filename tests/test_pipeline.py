"""流水线单元测试：plan_episode 在 mock LLM 下可跑通、create_episode 结构、v1.3/v1.4/v2.1 验收点。"""
from __future__ import annotations

import inspect
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.exceptions import PlanMappingError, PodcastAIError
from podcast_ai.core.models import (
    EpisodePlan,
    EpisodeRequest,
    EpisodeSegment,
    PlaylistItem,
    SegmentBoundary,
)
from podcast_ai.core.pipeline import create_episode, load_plan_from_disk, plan_episode, save_plan_to_disk
from podcast_ai.infra.audio_backend import is_ffmpeg_available, load_audio
from podcast_ai.infra.storage.paths import get_mix_output_path
from podcast_ai.infra.tts_client import TTSClient
from podcast_ai.modules.mastering.processor import MasteringService
from podcast_ai.modules.theme.state import validate_episode_snapshot_subset

_ffmpeg_required = pytest.mark.skipif(not is_ffmpeg_available(), reason="FFmpeg required")


def _mock_plan_json() -> str:
    """返回 LLM 模拟的 EpisodePlan JSON。"""
    return json.dumps(
        {
            "style_description": "Mock style",
            "overall_bpm_range": [90, 120],
            "segments": [
                {
                    "name": "开场",
                    "target_duration_seconds": 300,
                    "bpm_range": [90, 110],
                    "mood": "chill",
                    "host_script": "欢迎。",
                    "target_playlist": [
                        {"recommended_tracks": ["Track A"], "search_hints": {"genre": "chill"}},
                    ],
                },
            ],
        },
        ensure_ascii=False,
    )


def _save_stage2_snapshot_from_plan(output_dir: Path, episode_id: str, plan: EpisodePlan) -> Path:
    plans_dir = output_dir / "episodes" / episode_id / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "v3.0",
        "meta": {
            "request_id": f"req_{episode_id}",
            "theme": plan.style_description or "Episode",
            "language": "zh-CN",
            "target_duration_seconds": int(plan.target_duration_seconds),
        },
        "segments": [],
    }
    for idx, seg in enumerate(plan.segments):
        playlists: list[dict[str, str]] = []
        for item in seg.target_playlist:
            for rec in item.recommended_tracks:
                text = rec.strip()
                if " - " in text:
                    track, artist = text.split(" - ", 1)
                else:
                    track, artist = text, ""
                playlists.append({"track": track.strip(), "artist": artist.strip()})
        payload["segments"].append(
            {
                "segment_id": f"seg_{idx+1:02d}",
                "name": seg.name,
                "target_duration_seconds": int(seg.target_duration_seconds),
                "playlists": playlists,
                "script": {
                    "segment_intro": seg.host_script or "",
                    "between_tracks": [],
                },
            }
        )
    snapshot_path = plans_dir / f"{episode_id}.json"
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot_path


@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan_and_state")
def test_plan_episode_with_mock_llm(mock_generate_and_state: object, tmp_path: Path) -> None:
    """plan_episode 在 mock 下应正常完成并落盘（state.json + episode_id.json）。"""
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Mock style",
        plan_id="mock_plan",
    )

    request = EpisodeRequest(
        topic="Test",
        duration_minutes=10,
        language="zh",
        output_dir=tmp_path,
    )

    # state.json 校验要求 critic/control 在 multi_agent 模式下为 object
    from podcast_ai.modules.theme.state import initialize_plan_state

    mock_generate_and_state.return_value = (plan, initialize_plan_state(request))

    result_plan, state_path, snapshot_path = plan_episode(request)

    assert result_plan.plan_id
    assert state_path.exists()
    assert state_path.name == "state.json"
    assert snapshot_path.exists()
    assert snapshot_path.name.endswith(".json")
    assert snapshot_path.name != "state.json"
    assert snapshot_path.stem == state_path.parent.parent.name
    state_raw = json.loads(state_path.read_text(encoding="utf-8"))
    snapshot_raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    validate_episode_snapshot_subset(snapshot_raw)
    assert set(snapshot_raw.keys()) == {"schema", "meta", "segments"}
    assert set(snapshot_raw["meta"].keys()) == {"request_id", "theme", "language", "target_duration_seconds"}
    assert snapshot_raw["schema"] == state_raw["schema_version"]
    plans_dir = state_path.parent
    assert {p.name for p in plans_dir.glob("*.json")} == {"state.json", snapshot_path.name}
    assert not (state_path.parent.parent / "playlist.md").exists()


def test_save_and_load_plan_roundtrip(tmp_path: Path, sample_plan: EpisodePlan) -> None:
    """save_plan_to_disk / load_plan_from_disk 往返。"""
    from podcast_ai.infra.config import AppConfig, Settings

    settings = Settings(app=AppConfig(output_dir=str(tmp_path)))
    plan_path = save_plan_to_disk(sample_plan, settings=settings, episode_id="ep_test", plan_id="plan_test")
    loaded = load_plan_from_disk(plan_path)
    assert loaded.plan_id == sample_plan.plan_id
    assert len(loaded.segments) == len(sample_plan.segments)
    assert loaded.segments[0].name == sample_plan.segments[0].name


# ---------- v3.1：阶段一 state.json 生成/校验 ----------


@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan_and_state")
def test_v31_plan_episode_single_agent_outputs_valid_state_json(
    mock_generate_and_state: object,
    tmp_path: Path,
) -> None:
    from podcast_ai.core.models import EpisodeSegment
    from podcast_ai.modules.theme.state import validate_state_conforms_to_schema

    request = EpisodeRequest(topic="Test Single", duration_minutes=10, language="zh", output_dir=tmp_path)

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 100),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            )
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="single agent plan",
        plan_id="plan_single",
    )

    state = {
        "schema_version": "v3.0",
        "meta": {
            "request_id": "req_single",
            "theme": "Test Single",
            "theme_description": "single agent plan",
            "language": "zh-CN",
            "target_duration_seconds": 600,
            "overall_bpm_range": [90, 120],
        },
        "global_constraints": {
            "tone": "克制",
            "language_style": "第一人称",
            "avoid": [],
        },
        "plan": {
            "segments_design": "单段结构",
            "emotion_curve": ["平静"],
        },
        "segments": [
            {
                "segment_id": "seg_01",
                "order": 1,
                "name": "开场",
                "target_duration_seconds": 300,
                "bpm_range": [90, 100],
                "mood": "chill",
                "segment_design": "开场暖场",
                "playlist": [{"track": "Track A", "artist": "Artist A", "bpm": 95}],
                "script": {
                    "segment_intro": "欢迎来到节目。",
                    "between_tracks": [{"after_track_index": 0, "text": None}],
                },
            }
        ],
    }

    # pipeline 内部会做 schema 校验；这里直接确保返回的 state 处于合法 single_agent 形态
    validate_state_conforms_to_schema(state, agent_mode="single_agent")
    mock_generate_and_state.return_value = (plan, state)

    _, state_path, snapshot_path = plan_episode(request, agent_mode="single_agent")
    assert state_path.exists()
    assert snapshot_path.exists()
    assert not (state_path.parent.parent / "playlist.md").exists()

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    validate_state_conforms_to_schema(raw, agent_mode="single_agent")
    snapshot_raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    validate_episode_snapshot_subset(snapshot_raw)
    assert set(snapshot_raw.keys()) == {"schema", "meta", "segments"}
    assert set(snapshot_raw["meta"].keys()) == {"request_id", "theme", "language", "target_duration_seconds"}

    assert "critic" not in raw
    assert "control" not in raw


@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan_and_state")
def test_v31_plan_episode_multi_agent_outputs_valid_state_json(
    mock_generate_and_state: object,
    tmp_path: Path,
) -> None:
    from podcast_ai.modules.theme.state import (
        initialize_plan_state,
        validate_state_conforms_to_schema,
    )

    request = EpisodeRequest(topic="Test Multi", duration_minutes=10, language="zh", output_dir=tmp_path)

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 100),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            )
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="multi agent plan",
        plan_id="plan_multi",
    )

    state = initialize_plan_state(request)
    validate_state_conforms_to_schema(state, agent_mode="multi_agent")
    mock_generate_and_state.return_value = (plan, state)

    _, state_path, snapshot_path = plan_episode(request, agent_mode="multi_agent")
    assert state_path.exists()
    assert snapshot_path.exists()
    assert not (state_path.parent.parent / "playlist.md").exists()

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    validate_state_conforms_to_schema(raw, agent_mode="multi_agent")
    snapshot_raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    validate_episode_snapshot_subset(snapshot_raw)
    assert set(snapshot_raw.keys()) == {"schema", "meta", "segments"}
    assert set(snapshot_raw["meta"].keys()) == {"request_id", "theme", "language", "target_duration_seconds"}
    assert "global_constraints" not in snapshot_raw
    assert "plan" not in snapshot_raw

    assert isinstance(raw["critic"], dict)
    assert isinstance(raw["control"], dict)


@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan_and_state")
def test_v31_plan_episode_schema_validation_failure_blocks_output(
    mock_generate_and_state: object,
    tmp_path: Path,
) -> None:
    from podcast_ai.core.exceptions import AIServiceError
    from podcast_ai.core.models import EpisodeSegment
    from podcast_ai.modules.theme.state import initialize_plan_state

    request = EpisodeRequest(topic="Bad State", duration_minutes=10, language="zh", output_dir=tmp_path)
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 100),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            )
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="bad",
        plan_id="plan_bad",
    )

    bad_state = initialize_plan_state(request)
    bad_state["meta"]["target_duration_seconds"] = "not-int"  # type: ignore[assignment]

    mock_generate_and_state.return_value = (plan, bad_state)

    with pytest.raises(AIServiceError) as exc_info:
        plan_episode(request, agent_mode="multi_agent")

    assert "meta.target_duration_seconds" in str(exc_info.value)

    # schema 校验失败应阻断写入，不创建 state.json
    episodes_root = tmp_path / "episodes"
    assert not episodes_root.exists()


@patch("podcast_ai.core.pipeline.build_episode_snapshot_from_state")
@patch("podcast_ai.modules.theme.llm_planner.ThemePlanner.generate_plan_and_state")
def test_v38_plan_episode_snapshot_validation_failure_blocks_output(
    mock_generate_and_state: object,
    mock_build_snapshot: object,
    tmp_path: Path,
) -> None:
    from podcast_ai.core.exceptions import AIServiceError
    from podcast_ai.modules.theme.state import initialize_plan_state

    request = EpisodeRequest(topic="Bad Snapshot", duration_minutes=10, language="zh", output_dir=tmp_path)
    state = initialize_plan_state(request)
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 100),
                mood="chill",
                host_script="欢迎。",
                target_playlist=[],
            )
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="bad snapshot",
        plan_id="plan_bad_snapshot",
    )
    mock_generate_and_state.return_value = (plan, state)
    mock_build_snapshot.return_value = {"schema": "v3.0", "meta": {}, "segments": []}

    with pytest.raises(AIServiceError, match="snapshot.meta 字段不匹配"):
        plan_episode(request, agent_mode="multi_agent")


# ---------- v1.3 验收测试 ----------


def test_v13_voiceover_insert_time_from_actual_boundaries() -> None:
    """v1.3：串词 insert_time 基于 segment_boundaries 实际边界，而非 target_duration 累加。"""
    from podcast_ai.infra.config import AppConfig, Settings
    from podcast_ai.modules.voiceover.tts_service import VoiceoverService

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[PlaylistItem(segment_name="开场", recommended_tracks=[], search_hints={})],
            ),
            EpisodeSegment(
                name="主段",
                target_duration_seconds=300,
                bpm_range=(95, 115),
                mood="upbeat",
                host_script="",
                target_playlist=[PlaylistItem(segment_name="主段", recommended_tracks=[], search_hints={})],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test",
        plan_id="test",
    )
    # 实际边界：seg0 结束于 150s，seg1 结束于 400s（与 target_duration 300/300 不同）
    segment_boundaries = [
        SegmentBoundary(music_start=0.0, music_end=150.0),
        SegmentBoundary(music_start=150.0, music_end=400.0),
    ]
    settings = Settings(app=AppConfig(output_dir="./output"))
    svc = VoiceoverService(settings=settings)
    voiceovers = svc.generate_voiceovers(plan, segment_boundaries=segment_boundaries)
    assert len(voiceovers) == 2
    assert voiceovers[0].insert_time_in_episode == 0.0
    assert voiceovers[1].insert_time_in_episode == 150.0


@patch("podcast_ai.core.pipeline.LibraryScanner.scan_or_load_cache")
def test_v13_create_episode_mapping_failure_blocks(
    mock_scan: object,
    tmp_path: Path,
) -> None:
    """v1.3：segment 映射失败时 create_episode 抛出异常并阻断，不产生 mix 输出。"""
    from podcast_ai.core.models import Track, TrackMetadata, TrackWithMetadata
    from podcast_ai.infra.config import AppConfig, CacheConfig, Settings

    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    episode_id = "ep_test_unmapped"
    plan_id = "plan_test"
    episode_root = output_dir / "episodes" / episode_id
    plans_dir = episode_root / "plans"
    plans_dir.mkdir(parents=True)

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=300,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[
                    PlaylistItem(
                        segment_name="开场",
                        recommended_tracks=["NonExistentTrackXYZ"],
                        search_hints={},
                    )
                ],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="Test",
        plan_id=plan_id,
    )
    snapshot_path = _save_stage2_snapshot_from_plan(output_dir, episode_id, plan)

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    mock_scan.return_value = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=music_dir / "other.wav", title="OtherSong", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=120.0, bpm=95.0, genre=None),
        ),
    ]

    mix_path = get_mix_output_path(episode_root, ext="wav")

    with pytest.raises((PlanMappingError, PodcastAIError)):
        create_episode(
            snapshot_path,
            music_dir,
            settings=Settings(
                app=AppConfig(output_dir=str(output_dir)),
                cache=CacheConfig(enabled=False),
            ),
        )

    assert not mix_path.exists()


# ---------- v1.4：供应商切换与注入点 ----------


def test_v14_create_episode_accepts_tts_client_parameter() -> None:
    """create_episode 支持注入 tts_client，便于 mock ElevenLabs 或回归单测。"""
    sig = inspect.signature(create_episode)
    assert "tts_client" in sig.parameters


def test_v39_create_episode_invalid_snapshot_raises_clear_error(tmp_path: Path) -> None:
    bad_snapshot = {
        "schema": "v3.0",
        "meta": {
            "request_id": "r1",
            "theme": "t",
            # 缺 language
            "target_duration_seconds": 600,
        },
        "segments": [],
    }
    snap_path = tmp_path / "episodes" / "ep_bad" / "plans" / "ep_bad.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(bad_snapshot, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PodcastAIError, match="snapshot 结构校验失败"):
        create_episode(snap_path, tmp_path / "music")


@patch("podcast_ai.core.pipeline.Exporter.export_episode")
@patch("podcast_ai.core.pipeline.MasteringService.apply_mastering")
@patch("podcast_ai.core.pipeline.Mixer.build_mix")
@patch("podcast_ai.core.pipeline.LibraryScanner.scan_or_load_cache")
def test_v39_create_episode_between_tracks_generates_extra_voiceovers(
    mock_scan: object,
    mock_build_mix: object,
    mock_mastering: object,
    mock_export: object,
    tmp_path: Path,
) -> None:
    from podcast_ai.core.models import EpisodeResult, Track, TrackMetadata, TrackWithMetadata
    from podcast_ai.infra.config import AppConfig, CacheConfig, Settings

    output_dir = tmp_path / "out"
    music_dir = tmp_path / "music"
    output_dir.mkdir(parents=True)
    music_dir.mkdir()
    t1 = music_dir / "a.wav"
    t2 = music_dir / "b.wav"
    _export_short_wav(t1, 1_000)
    _export_short_wav(t2, 1_000)
    episode_id = "ep_bt"
    snapshot = {
        "schema": "v3.0",
        "meta": {
            "request_id": "r_bt",
            "theme": "between tracks",
            "language": "zh-CN",
            "target_duration_seconds": 600,
        },
        "segments": [
            {
                "segment_id": "seg_01",
                "name": "开场",
                "target_duration_seconds": 300,
                "playlists": [{"track": "A", "artist": "X"}, {"track": "B", "artist": "Y"}],
                "script": {
                    "segment_intro": "intro",
                    "between_tracks": [
                        {"after_track_index": 0, "text": "between"},
                        {"after_track_index": 1, "text": None},
                    ],
                },
            }
        ],
    }
    snapshot_path = output_dir / "episodes" / episode_id / "plans" / f"{episode_id}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    mock_scan.return_value = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=t1, title="A", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=1.0, bpm=95.0, genre=None),
        ),
        TrackWithMetadata(
            track=Track(id="t2", file_path=t2, title="B", artist="Y"),
            metadata=TrackMetadata(track_id="t2", duration_seconds=1.0, bpm=96.0, genre=None),
        ),
    ]
    mock_build_mix.return_value = MagicMock(
        mix_path=output_dir / "episodes" / episode_id / "mix" / "mix.wav",
        actual_duration_seconds=2.0,
        track_count=2,
        voiceover_count=2,
    )
    mock_mastering.side_effect = lambda mix_path, output_path, _config: output_path
    mock_export.return_value = EpisodeResult(
        episode_id=episode_id,
        audio_path=output_dir / "episodes" / episode_id / "final" / f"{episode_id}.mp3",
        actual_duration_seconds=2,
        show_notes="ok",
        tracks=[],
    )

    class _StubTTS(TTSClient):
        def __init__(self, base: Path) -> None:
            self._base = base
            self._i = 0

        def synthesize(self, text: str, **kwargs: object) -> Path:  # type: ignore[override]
            self._i += 1
            p = self._base / f"vo_{self._i}.wav"
            _export_short_wav(p, 200)
            return p

    create_episode(
        snapshot_path,
        music_dir,
        settings=Settings(app=AppConfig(output_dir=str(output_dir)), cache=CacheConfig(enabled=False)),
        tts_client=_StubTTS(tmp_path),
    )

    _args, kwargs = mock_build_mix.call_args
    voiceovers = _args[1]
    assert len(voiceovers) == 2
    assert [v.text for v in voiceovers] == ["intro", "between"]


# ---------- v2.1：pipeline 集成回归（边界仅音乐淡入 + 顺序/产物；时长断言带合理容差） ----------


class _FixedFileTTSClient(TTSClient):
    """单测用：跳过网络，始终返回预先写好的 wav 路径。"""

    def __init__(self, audio_path: Path) -> None:
        self._audio_path = audio_path

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
        use_cache: bool = True,
    ) -> Path:
        return self._audio_path


def _export_short_wav(path: Path, duration_ms: int) -> None:
    AudioSegment.silent(duration=duration_ms).export(str(path), format="wav")


@_ffmpeg_required
@patch.object(MasteringService, "apply_mastering")
@patch("podcast_ai.core.pipeline.LibraryScanner.scan_or_load_cache")
def test_v21_create_episode_happy_path_preserves_order_and_outputs(
    mock_scan: object,
    mock_mastering: object,
    tmp_path: Path,
) -> None:
    """
    v2.1：voice_music_crossfade>0 时 create_episode 仍可跑通；选曲顺序与输出路径不变。

    本用例 plan 无 host_script，串词为极短占位（约 1ms）；首段叠化窗口被截断为约 1ms，
    总时长≈单曲有效时长 2.5s（与 v2.0 对称叠化在此场景下数值接近，改用更紧的区间而非宽松上界 3s）。
    """
    from podcast_ai.core.models import Track, TrackMetadata, TrackWithMetadata
    from podcast_ai.infra.config import (
        AppConfig,
        AudioConfig,
        CacheConfig,
        ElevenLabsConfig,
        Settings,
        TTSConfig,
    )

    vm = 1.0
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    episode_id = "ep_test_v21_happy"
    plan_id = "plan_v21_happy"

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    track_path = music_dir / "Track A.wav"
    _export_short_wav(track_path, 2_500)

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=120,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[
                    PlaylistItem(
                        segment_name="开场",
                        recommended_tracks=["Track A"],
                        search_hints={},
                    )
                ],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="v21 pipeline regression",
        plan_id=plan_id,
    )
    snapshot_path = _save_stage2_snapshot_from_plan(output_dir, episode_id, plan)

    mock_scan.return_value = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=track_path, title="Track A", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=2.5, bpm=95.0, genre=None),
        ),
    ]

    def _copy_master(mix_path: Path, output_path: Path, _config: object) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(mix_path, output_path)
        return output_path

    mock_mastering.side_effect = _copy_master

    settings = Settings(
        app=AppConfig(output_dir=str(output_dir)),
        audio=AudioConfig(
            crossfade_seconds=0.0,
            voice_music_crossfade_seconds=vm,
            loudness_target_lufs=-14.0,
        ),
        cache=CacheConfig(enabled=False),
        tts=TTSConfig(
            provider="elevenlabs",
            elevenlabs=ElevenLabsConfig(api_key="test", voice_id="vid"),
        ),
    )

    result = create_episode(snapshot_path, music_dir, settings=settings, topic="Regression")

    assert result.episode_id == episode_id
    assert len(result.tracks) == 1
    assert result.tracks[0].track.title == "Track A"
    assert result.audio_path.exists()
    assert result.actual_duration_seconds >= 0
    mix_path = get_mix_output_path(snapshot_path.parent.parent, ext="wav")
    assert mix_path.exists()
    music_seconds = 2.5
    # 波形时长用毫秒精度；EpisodeResult.actual_duration_seconds 为 int 截断秒
    mix_dur_s = len(load_audio(mix_path)) / 1000.0
    assert abs(mix_dur_s - music_seconds) <= 0.15
    assert result.actual_duration_seconds == int(mix_dur_s)


@_ffmpeg_required
@patch.object(MasteringService, "apply_mastering")
@patch("podcast_ai.core.pipeline.LibraryScanner.scan_or_load_cache")
def test_v21_create_episode_duration_matches_voice_music_overlap(
    mock_scan: object,
    mock_mastering: object,
    tmp_path: Path,
) -> None:
    """
    注入固定时长串词 wav，避免依赖网络；总时长应满足 v2.1 首段公式
    len(vo)+len(music)-min(vm, len(vo), len(music))（单 segment、组内无歌曲 crossfade）。
    """
    from podcast_ai.core.models import Track, TrackMetadata, TrackWithMetadata
    from podcast_ai.infra.config import (
        AppConfig,
        AudioConfig,
        CacheConfig,
        ElevenLabsConfig,
        Settings,
        TTSConfig,
    )

    vm_sec = 1.0
    vo_ms = 3_000
    music_ms = 2_500
    overlap_ms = min(int(vm_sec * 1000), vo_ms, music_ms)
    expected_mix_ms = vo_ms + music_ms - overlap_ms

    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    episode_id = "ep_test_v21_dur"
    plan_id = "plan_v21_dur"

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    track_path = music_dir / "Track A.wav"
    _export_short_wav(track_path, music_ms)

    vo_path = tmp_path / "stub_vo.wav"
    _export_short_wav(vo_path, vo_ms)

    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=120,
                bpm_range=(90, 110),
                mood="chill",
                host_script="串词占位",
                target_playlist=[
                    PlaylistItem(
                        segment_name="开场",
                        recommended_tracks=["Track A"],
                        search_hints={},
                    )
                ],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="v21 duration",
        plan_id=plan_id,
    )
    snapshot_path = _save_stage2_snapshot_from_plan(output_dir, episode_id, plan)

    mock_scan.return_value = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=track_path, title="Track A", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=music_ms / 1000.0, bpm=95.0, genre=None),
        ),
    ]

    def _copy_master(mix_path: Path, output_path: Path, _config: object) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(mix_path, output_path)
        return output_path

    mock_mastering.side_effect = _copy_master

    settings = Settings(
        app=AppConfig(output_dir=str(output_dir)),
        audio=AudioConfig(
            crossfade_seconds=0.0,
            voice_music_crossfade_seconds=vm_sec,
            loudness_target_lufs=-14.0,
        ),
        cache=CacheConfig(enabled=False),
        tts=TTSConfig(
            provider="elevenlabs",
            elevenlabs=ElevenLabsConfig(api_key="test", voice_id="vid"),
        ),
    )

    tts_stub = _FixedFileTTSClient(vo_path)
    create_episode(snapshot_path, music_dir, settings=settings, topic="Dur", tts_client=tts_stub)

    mix_path = get_mix_output_path(snapshot_path.parent.parent, ext="wav")
    mix_dur_ms = len(load_audio(mix_path))
    assert abs(mix_dur_ms - expected_mix_ms) <= 80


@patch.object(MasteringService, "apply_mastering")
@patch("podcast_ai.core.pipeline.Mixer.build_mix")
@patch("podcast_ai.core.pipeline.LibraryScanner.scan_or_load_cache")
def test_v21_create_episode_passes_voice_music_crossfade_to_mixer(
    mock_scan: object,
    mock_build_mix: object,
    mock_mastering: object,
    tmp_path: Path,
) -> None:
    """v2.1：pipeline 构造的 AudioRenderConfig 含 voice_music_crossfade，并传给 Mixer。"""
    from podcast_ai.core.models import Track, TrackMetadata, TrackWithMetadata
    from podcast_ai.infra.config import (
        AppConfig,
        AudioConfig,
        CacheConfig,
        ElevenLabsConfig,
        Settings,
        TTSConfig,
    )

    output_dir = tmp_path / "out"
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    track_path = music_dir / "a.wav"
    track_path.write_bytes(b"x")

    episode_id = "ep_cfg"
    plan_id = "plan_cfg"
    plan = EpisodePlan(
        segments=[
            EpisodeSegment(
                name="开场",
                target_duration_seconds=120,
                bpm_range=(90, 110),
                mood="chill",
                host_script="",
                target_playlist=[
                    PlaylistItem(segment_name="开场", recommended_tracks=["a"], search_hints={}),
                ],
            ),
        ],
        target_duration_seconds=600,
        overall_bpm_range=(90, 120),
        style_description="cfg",
        plan_id=plan_id,
    )
    snapshot_path = _save_stage2_snapshot_from_plan(output_dir, episode_id, plan)
    mock_scan.return_value = [
        TrackWithMetadata(
            track=Track(id="t1", file_path=track_path, title="a", artist="X"),
            metadata=TrackMetadata(track_id="t1", duration_seconds=1.0, bpm=95.0, genre=None),
        ),
    ]
    mix_out = get_mix_output_path(snapshot_path.parent.parent, ext="wav")
    mock_build_mix.return_value = MagicMock(
        mix_path=mix_out,
        actual_duration_seconds=1.0,
        track_count=1,
        voiceover_count=1,
    )

    def _stub_master(mix_path: Path, output_path: Path, _config: object) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return output_path

    mock_mastering.side_effect = _stub_master

    vm = 2.5
    settings = Settings(
        app=AppConfig(output_dir=str(output_dir)),
        audio=AudioConfig(voice_music_crossfade_seconds=vm, crossfade_seconds=0.0),
        cache=CacheConfig(enabled=False),
        tts=TTSConfig(provider="elevenlabs", elevenlabs=ElevenLabsConfig(api_key="k", voice_id="v")),
    )

    create_episode(snapshot_path, music_dir, settings=settings)

    assert mock_build_mix.called
    _args, kwargs = mock_build_mix.call_args
    cfg = _args[2]
    assert cfg.voice_music_crossfade_seconds == vm


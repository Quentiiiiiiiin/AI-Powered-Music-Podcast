"""v1.4：ElevenLabs 错误分类（Task 03）与合成结果可被音频后端加载（Task 05）。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.exceptions import AIServiceError, TTSServiceError
from podcast_ai.infra.config import AppConfig, ElevenLabsConfig, Settings, TTSConfig
from podcast_ai.infra.audio_backend import is_ffmpeg_available, load_audio
from podcast_ai.infra.tts_client import ElevenLabsTTSClient

_ffmpeg_required = pytest.mark.skipif(not is_ffmpeg_available(), reason="FFmpeg required")


def _client() -> ElevenLabsTTSClient:
    s = Settings(
        app=AppConfig(output_dir="."),
        tts=TTSConfig(
            provider="elevenlabs",
            max_retries=1,
            elevenlabs=ElevenLabsConfig(api_key="k", voice_id="voice_1"),
        ),
    )
    return ElevenLabsTTSClient(s.tts, s)


def test_elevenlabs_401_ttsserviceerror_message() -> None:
    """401 → 鉴权失败类说明。"""
    c = _client()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    mock_resp.text = '{"detail":{"status":"invalid_api_key","message":"bad"}}'

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp

        with pytest.raises(TTSServiceError, match="鉴权失败"):
            c.synthesize("hello", use_cache=False)


def test_elevenlabs_429_quota_message() -> None:
    """429 → 配额/限流类说明。"""
    c = _client()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.text = "Too Many Requests"

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp

        with pytest.raises(TTSServiceError, match="配额或频率受限"):
            c.synthesize("hello", use_cache=False)


def test_elevenlabs_timeout_ttsserviceerror() -> None:
    """超时 → 明确提示 timeout / timeout_seconds。"""
    c = _client()

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(TTSServiceError, match="超时"):
            c.synthesize("hello", use_cache=False)


def test_elevenlabs_200_json_body_raises_not_audio() -> None:
    """200 但正文为 JSON → 视为格式异常，不静默写文件。"""
    c = _client()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = '{"error":true}'
    mock_resp.content = b'{"detail":"oops"}'

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp

        with pytest.raises(TTSServiceError, match="期望音频数据"):
            c.synthesize("hello", use_cache=False)


def test_elevenlabs_200_valid_mp3_writes_file(tmp_path: Path) -> None:
    """200 且为 mp3 二进制时写入缓存并返回路径。"""
    mp3_like = b"\xff\xfb\x90\x00" + b"\x00" * 64  # 简单 mp3 帧头近似
    s = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(
            provider="elevenlabs",
            max_retries=1,
            elevenlabs=ElevenLabsConfig(api_key="k", voice_id="voice_1"),
        ),
    )
    c = ElevenLabsTTSClient(s.tts, s)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.content = mp3_like

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp

        out = c.synthesize("hello", use_cache=False)
        assert out.exists()
        assert out.read_bytes()[:4] == mp3_like[:4]


@_ffmpeg_required
def test_v14_elevenlabs_synthesize_output_loadable_by_audio_backend(tmp_path: Path) -> None:
    """Task 05：mock API 返回合法 mp3 字节时，落盘文件可被 load_audio 消费（与混音入口一致）。"""
    ref_mp3 = tmp_path / "ref.mp3"
    AudioSegment.silent(duration=120).export(str(ref_mp3), format="mp3")
    mp3_bytes = ref_mp3.read_bytes()

    s = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(
            provider="elevenlabs",
            max_retries=1,
            elevenlabs=ElevenLabsConfig(api_key="k", voice_id="voice_1"),
        ),
    )
    c = ElevenLabsTTSClient(s.tts, s)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.content = mp3_bytes

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp

        out = c.synthesize("hello from v14", use_cache=False)
        audio = load_audio(out)
        assert len(audio) > 0


def test_ttsserviceerror_is_aiservice_error() -> None:
    """TTSServiceError 可视为 AIServiceError 统一捕获。"""
    assert issubclass(TTSServiceError, AIServiceError)

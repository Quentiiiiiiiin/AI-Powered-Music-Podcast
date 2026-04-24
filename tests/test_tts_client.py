from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from podcast_ai.core.exceptions import TTSServiceError
from podcast_ai.infra.config import AppConfig, MiniMaxConfig, Settings, TTSConfig
from podcast_ai.infra.tts_client import MiniMaxTTSClient


def _minimax_client(tmp_path: Path) -> MiniMaxTTSClient:
    s = Settings(
        app=AppConfig(output_dir=str(tmp_path)),
        tts=TTSConfig(
            provider="minimax",
            max_retries=1,
            minimax=MiniMaxConfig(
                api_key="mm_key",
                model="speech-2.8-hd",
                voice_id="male-qn-qingse",
            ),
        ),
    )
    return MiniMaxTTSClient(s.tts, s)


def test_minimax_synthesize_success_hex_to_audio_file(tmp_path: Path) -> None:
    c = _minimax_client(tmp_path)
    audio_bytes = b"\xff\xfb\x90\x00" + b"\x00" * 64
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json.return_value = {
        "data": {"audio": audio_bytes.hex(), "status": 2},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp

        out = c.synthesize("hello minimax", use_cache=False)
        assert out.exists()
        assert out.read_bytes()[:4] == audio_bytes[:4]


def test_minimax_status_code_failure_raises_tts_error(tmp_path: Path) -> None:
    c = _minimax_client(tmp_path)
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json.return_value = {
        "data": {"audio": "00", "status": 2},
        "base_resp": {"status_code": 1004, "status_msg": "invalid key"},
    }

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp
        with pytest.raises(TTSServiceError, match="失败码"):
            c.synthesize("hello minimax", use_cache=False)


def test_minimax_missing_audio_field_raises_tts_error(tmp_path: Path) -> None:
    c = _minimax_client(tmp_path)
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json.return_value = {
        "data": {"status": 2},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp
        with pytest.raises(TTSServiceError, match="data.audio"):
            c.synthesize("hello minimax", use_cache=False)


def test_minimax_invalid_hex_raises_tts_error(tmp_path: Path) -> None:
    c = _minimax_client(tmp_path)
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json.return_value = {
        "data": {"audio": "this-is-not-hex", "status": 2},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }

    with patch("podcast_ai.infra.tts_client.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.return_value = mock_resp
        with pytest.raises(TTSServiceError, match="hex"):
            c.synthesize("hello minimax", use_cache=False)

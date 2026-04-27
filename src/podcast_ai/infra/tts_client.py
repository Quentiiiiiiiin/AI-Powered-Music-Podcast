from __future__ import annotations

import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

import edge_tts  # type: ignore[import-untyped]
import httpx

from podcast_ai.core.exceptions import AIServiceError, TTSServiceError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.infra.config import (
    ElevenLabsConfig,
    MiniMaxConfig,
    Settings,
    TTSConfig,
    load_settings,
    require_elevenlabs_tts_config,
    require_minimax_tts_config,
)
from podcast_ai.infra.storage.paths import get_tts_cache_dir, get_tts_cache_file

logger = logging.getLogger(__name__)

_ELEVENLABS_API_BASE = "https://api.elevenlabs.io"
_MINIMAX_TTS_URL = "https://api.minimaxi.com/v1/t2a_v2"


def _truncate_detail(text: str, max_len: int = 1200) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _normalize_tts_provider(provider: str) -> str:
    raw = (provider or "").strip().lower()
    aliases = {"edge_tts": "edge", "edge-tts": "edge"}
    return aliases.get(raw, raw)


def _cache_ext_for_output_format(output_format: str) -> str:
    prefix = (output_format or "").strip().lower().split("_", 1)[0]
    if prefix in {"wav", "mp3"}:
        return prefix
    return "mp3"


def _build_cache_path(
    *,
    output_dir: Path,
    provider: str,
    voice_key: str,
    text: str,
    ext: str,
    extra_keys: list[str] | None = None,
) -> Path:
    cache_dir = get_tts_cache_dir(output_dir)
    h = hashlib.sha256()
    h.update(provider.encode("utf-8", errors="ignore"))
    h.update(b"\0")
    h.update(voice_key.encode("utf-8", errors="ignore"))
    h.update(b"\0")
    for k in extra_keys or []:
        h.update(k.encode("utf-8", errors="ignore"))
        h.update(b"\0")
    h.update(text.encode("utf-8", errors="ignore"))
    text_hash = h.hexdigest()[:32]
    safe_voice = voice_key.replace("/", "_").replace("\\", "_") or "default"
    return get_tts_cache_file(cache_dir, voice=safe_voice, text_hash=text_hash, ext=ext)


def _validate_audio_bytes(content: bytes, provider_name: str) -> None:
    if not content:
        raise TTSServiceError(f"{provider_name} TTS：响应体为空，无法写入音频缓存。")
    stripped = content.lstrip()
    if stripped[:1] in (b"{", b"["):
        snippet = content.decode("utf-8", errors="replace")
        raise TTSServiceError(
            f"{provider_name} TTS：期望音频数据，但响应体为 JSON/文本。"
            f" 片段：{_truncate_detail(snippet, 800)!r}"
        )
    if re.match(br"^\s*<", content):
        snippet = content.decode("utf-8", errors="replace")
        raise TTSServiceError(
            f"{provider_name} TTS：期望音频数据，但响应体疑似 HTML。"
            f" 片段：{_truncate_detail(snippet, 400)!r}"
        )


def _write_audio_bytes(dest: Path, content: bytes, provider_name: str) -> None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    except OSError as exc:
        raise TTSServiceError(
            f"{provider_name} TTS：音频已返回，但写入缓存文件失败：{dest}。详情：{exc!s}"
        ) from exc


def _retry_synthesize(
    *,
    provider_name: str,
    max_retries: int,
    action: Callable[[int], Path],
) -> Path:
    attempts = max(max_retries, 1)
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action(attempt)
        except TTSServiceError as exc:
            last_error = exc
            logger.warning("%s TTS 调用失败（第 %d 次）：%s", provider_name, attempt, repr(exc))
    raise TTSServiceError(
        f"{provider_name} TTS：在配置的重试次数内仍未成功。最后一次错误：{last_error!s}"
    ) from last_error


def _parse_elevenlabs_error_body(body: str) -> str:
    raw = (body or "").strip()
    if not raw:
        return ""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    detail = data.get("detail")
    if isinstance(detail, dict):
        if detail.get("message"):
            return str(detail["message"])
        if detail.get("status"):
            return str(detail["status"])
    if isinstance(detail, list) and detail and isinstance(detail[0], dict) and detail[0].get("msg"):
        return str(detail[0]["msg"])
    if isinstance(detail, str):
        return detail
    return ""


def _elevenlabs_http_to_tts_error(resp: httpx.Response, *, voice_id: str, model_id: str) -> TTSServiceError:
    status = resp.status_code
    body = _truncate_detail(resp.text or "")
    parsed = _parse_elevenlabs_error_body(resp.text or "")

    if status == 401:
        reason = "鉴权失败：请检查 tts.elevenlabs.api_key / xi-api-key 是否正确且未过期。"
    elif status == 403:
        reason = "权限不足：当前 API Key 可能无权使用该 voice 或该功能。"
    elif status == 404:
        reason = "资源不存在：voice_id 或接口路径可能无效，请核对控制台中的 Voice ID。"
    elif status == 422:
        reason = "请求参数校验失败：请检查 model_id、text 长度与 output_format 等是否合法。"
    elif status == 429:
        reason = "配额或频率受限：请稍后重试、升级套餐，或降低并发。"
    elif 500 <= status <= 599:
        reason = "ElevenLabs 服务端异常：请稍后重试。"
    else:
        reason = f"HTTP {status}。"

    suffix = f" 接口说明：{parsed}" if parsed else ""
    if not suffix and body:
        suffix = f" 响应片段：{body!r}"

    return TTSServiceError(
        f"ElevenLabs TTS 调用失败：{reason}（voice_id={voice_id!r} model_id={model_id!r}）。{suffix}"
    )


class TTSClient(ABC):
    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,
        use_cache: bool = True,
    ) -> Path:
        """将文本转换为语音文件并返回文件路径。"""


class ElevenLabsTTSClient(TTSClient):
    def __init__(self, cfg: TTSConfig, settings: Settings) -> None:
        self._cfg = cfg
        self._settings = settings

    def _request_once(self, *, text: str, el: ElevenLabsConfig, voice_id: str) -> bytes:
        url = f"{_ELEVENLABS_API_BASE}/v1/text-to-speech/{voice_id}"
        timeout = max(float(self._cfg.timeout_seconds), 5.0)
        headers = {
            "xi-api-key": el.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload: dict[str, str] = {"text": text, "model_id": el.model}
        params = {"output_format": el.output_format}

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=payload, params=params)
        except httpx.TimeoutException as exc:
            raise TTSServiceError(
                "ElevenLabs TTS：请求超时。"
                f"可检查网络、代理，或增大配置项 tts.timeout_seconds（当前约 {timeout:.0f}s）。"
            ) from exc
        except httpx.RequestError as exc:
            raise TTSServiceError(
                f"ElevenLabs TTS：网络请求失败（{type(exc).__name__}）。详情：{exc!s}"
            ) from exc

        if resp.status_code != 200:
            raise _elevenlabs_http_to_tts_error(resp, voice_id=voice_id, model_id=el.model)
        return resp.content

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,  # noqa: ARG002
        use_cache: bool = True,
    ) -> Path:
        if not (text or "").strip():
            raise TTSServiceError("ElevenLabs TTS：合成文本为空。")

        el = require_elevenlabs_tts_config(self._cfg)
        voice_id = (voice or "").strip() or el.voice_id
        ext = _cache_ext_for_output_format(el.output_format)
        cache_path = _build_cache_path(
            output_dir=Path(self._settings.app.output_dir),
            provider="elevenlabs",
            voice_key=voice_id,
            text=text,
            ext=ext,
            extra_keys=[el.model, el.output_format],
        )
        if use_cache and cache_path.exists():
            logger.debug("命中 TTS 缓存：%s", cache_path)
            return cache_path

        def _action(attempt: int) -> Path:
            with log_timing(logger, f"tts_elevenlabs_attempt_{attempt}"):
                content = self._request_once(text=text, el=el, voice_id=voice_id)
            _validate_audio_bytes(content, "ElevenLabs")
            _write_audio_bytes(cache_path, content, "ElevenLabs")
            return cache_path

        return _retry_synthesize(
            provider_name="ElevenLabs",
            max_retries=self._cfg.max_retries,
            action=_action,
        )


class MiniMaxTTSClient(TTSClient):
    """MiniMax 同步 TTS（v4.1）：/v1/t2a_v2，stream=false，返回 data.audio(hex)。"""

    def __init__(self, cfg: TTSConfig, settings: Settings) -> None:
        self._cfg = cfg
        self._settings = settings

    def _request_once(self, *, text: str, mm: MiniMaxConfig, voice_id: str) -> bytes:
        timeout = max(float(self._cfg.timeout_seconds), 5.0)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {mm.api_key}",
        }
        # v4.1 约束：voice_setting/audio_setting 固定默认值；后续再配置化
        payload: dict[str, Any] = {
            "model": mm.model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
                # "emotion": "happy",
            },
            "audio_setting": {
                "sample_rate": 44100,
                "bitrate": 256000,
                "format": "mp3",
                "channel": 1,
            },
            "subtitle_enable": False,
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(_MINIMAX_TTS_URL, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise TTSServiceError(
                "MiniMax TTS：请求超时。"
                f"可检查网络或增大配置项 tts.timeout_seconds（当前约 {timeout:.0f}s）。"
            ) from exc
        except httpx.RequestError as exc:
            raise TTSServiceError(
                f"MiniMax TTS：网络请求失败（{type(exc).__name__}）。详情：{exc!s}"
            ) from exc

        if resp.status_code != 200:
            raise TTSServiceError(
                "MiniMax TTS：HTTP 调用失败。"
                f" status={resp.status_code} body={_truncate_detail(resp.text or '', 800)!r}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise TTSServiceError("MiniMax TTS：响应不是合法 JSON。") from exc

        if not isinstance(data, dict):
            raise TTSServiceError("MiniMax TTS：响应结构非法（顶层非 object）。")

        base_resp = data.get("base_resp")
        if not isinstance(base_resp, dict):
            raise TTSServiceError("MiniMax TTS：响应缺少 base_resp。")
        status_code = base_resp.get("status_code")
        if status_code != 0:
            raise TTSServiceError(
                "MiniMax TTS：服务返回失败码。"
                f" status_code={status_code!r} status_msg={base_resp.get('status_msg')!r}"
            )

        payload_data = data.get("data")
        if not isinstance(payload_data, dict):
            raise TTSServiceError("MiniMax TTS：响应缺少 data。")
        audio_hex = payload_data.get("audio")
        if not isinstance(audio_hex, str) or not audio_hex.strip():
            raise TTSServiceError("MiniMax TTS：响应缺少 data.audio。")

        try:
            return bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise TTSServiceError("MiniMax TTS：data.audio 不是合法 hex 编码。") from exc

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,  # noqa: ARG002
        use_cache: bool = True,
    ) -> Path:
        if not (text or "").strip():
            raise TTSServiceError("MiniMax TTS：合成文本为空。")

        mm = require_minimax_tts_config(self._cfg)
        voice_id = (voice or "").strip() or mm.voice_id
        cache_path = _build_cache_path(
            output_dir=Path(self._settings.app.output_dir),
            provider="minimax",
            voice_key=voice_id,
            text=text,
            ext="mp3",
            extra_keys=[mm.model],
        )
        if use_cache and cache_path.exists():
            logger.debug("命中 TTS 缓存：%s", cache_path)
            return cache_path

        def _action(attempt: int) -> Path:
            with log_timing(logger, f"tts_minimax_attempt_{attempt}"):
                content = self._request_once(text=text, mm=mm, voice_id=voice_id)
            _validate_audio_bytes(content, "MiniMax")
            _write_audio_bytes(cache_path, content, "MiniMax")
            return cache_path

        return _retry_synthesize(
            provider_name="MiniMax",
            max_retries=self._cfg.max_retries,
            action=_action,
        )


class EdgeTTSSimpleClient(TTSClient):
    def __init__(self, cfg: TTSConfig, settings: Settings) -> None:
        self._cfg = cfg
        self._settings = settings

    async def _synthesize_once_async(self, text: str, voice: str, dest: Path) -> None:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await communicate.save(str(dest))

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,  # noqa: ARG002
        use_cache: bool = True,
    ) -> Path:
        import asyncio

        if not (text or "").strip():
            raise TTSServiceError("Edge TTS：合成文本为空。")

        effective_voice = (voice or self._cfg.voice or "").strip()
        if not effective_voice:
            raise AIServiceError("TTS voice 未配置。请在 config.yaml 或环境变量中设置 tts.voice。")

        cache_path = _build_cache_path(
            output_dir=Path(self._settings.app.output_dir),
            provider="edge",
            voice_key=effective_voice,
            text=text,
            ext="mp3",
        )
        if use_cache and cache_path.exists():
            logger.debug("命中 TTS 缓存：%s", cache_path)
            return cache_path

        attempts = max(self._cfg.max_retries, 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with log_timing(logger, f"tts_edge_attempt_{attempt}"):
                    asyncio.run(self._synthesize_once_async(text, effective_voice, cache_path))
                return cache_path
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("Edge TTS 调用失败（第 %d 次）：%s", attempt, repr(exc))
        raise AIServiceError("Edge TTS 多次重试后仍然失败。") from last_error


def get_default_tts_client(settings: Settings | None = None) -> TTSClient:
    s = settings or load_settings()
    cfg = s.tts
    prov = _normalize_tts_provider(cfg.provider)
    if prov == "elevenlabs":
        return ElevenLabsTTSClient(cfg, s)
    if prov == "edge":
        return EdgeTTSSimpleClient(cfg, s)
    if prov == "minimax":
        return MiniMaxTTSClient(cfg, s)
    raise AIServiceError(
        f"暂不支持的 TTS provider：{cfg.provider!r}（支持：edge、elevenlabs、minimax）"
    )

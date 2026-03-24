from __future__ import annotations

import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import edge_tts  # type: ignore[import-untyped]
import httpx

from podcast_ai.core.exceptions import AIServiceError, TTSServiceError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.infra.config import (
    ElevenLabsConfig,
    Settings,
    TTSConfig,
    load_settings,
    require_elevenlabs_tts_config,
)
from podcast_ai.infra.storage.paths import (
    get_tts_cache_dir,
    get_tts_cache_file,
)

logger = logging.getLogger(__name__)

# ElevenLabs 官方 REST（与文档一致；不引入额外 SDK 依赖）
_ELEVENLABS_API_BASE = "https://api.elevenlabs.io"


def _truncate_detail(text: str, max_len: int = 1200) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _parse_elevenlabs_error_body(body: str) -> str:
    """从 ElevenLabs JSON 错误体中提取可读说明（失败时返回空串）。"""
    raw = (body or "").strip()
    if not raw:
        return ""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            msg = detail.get("message")
            status = detail.get("status")
            if msg:
                return str(msg)
            if status:
                return str(status)
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        if isinstance(detail, str):
            return detail
    return ""


def _elevenlabs_http_to_tts_error(
    resp: httpx.Response,
    *,
    voice_id: str,
    model_id: str,
) -> TTSServiceError:
    """将非 2xx 响应转为带分类说明的 TTSServiceError（不抛出 HTTP 异常）。"""
    status = resp.status_code
    body = _truncate_detail(resp.text or "")
    parsed = _parse_elevenlabs_error_body(resp.text or "")

    reason: str
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

    msg = (
        f"ElevenLabs TTS 调用失败：{reason}"
        f"（voice_id={voice_id!r} model_id={model_id!r}）。"
        f"{suffix}"
    )
    return TTSServiceError(msg)


def _verify_audio_response_body(content: bytes, output_format: str) -> None:
    """成功响应应为音频二进制；若明显为 JSON/HTML 则报错，避免静默写入坏文件。"""
    if not content:
        raise TTSServiceError(
            "ElevenLabs TTS：HTTP 200 但响应体为空，无法写入缓存。"
            f"请检查 voice_id、model_id 与 output_format（当前 {output_format!r}）。"
        )
    stripped = content.lstrip()
    if stripped[:1] in (b"{", b"["):
        snippet = content.decode("utf-8", errors="replace")
        raise TTSServiceError(
            "ElevenLabs TTS：期望音频数据，但响应体为 JSON/文本（可能为错误被误标为 200）。"
            f" 片段：{_truncate_detail(snippet, 800)!r}"
        )
    if re.match(br"^\s*<", content):
        snippet = content.decode("utf-8", errors="replace")
        raise TTSServiceError(
            "ElevenLabs TTS：期望音频数据，但响应体疑似 HTML。"
            f" 片段：{_truncate_detail(snippet, 400)!r}"
        )


def _normalize_tts_provider(provider: str) -> str:
    return (provider or "").strip().lower()


def _cache_ext_for_output_format(output_format: str) -> str:
    """缓存文件扩展名：当前配置以 mp3 为主，与混音链路 pydub 加载兼容。"""
    prefix = (output_format or "").strip().lower().split("_", 1)[0]
    if prefix == "wav":
        return "wav"
    if prefix == "mp3":
        return "mp3"
    # opus/pcm 等可后续按需扩展；默认按 mp3 落盘
    return "mp3"


class TTSClient(ABC):
    """TTSClient 抽象接口。"""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        language: Optional[str] = None,
        use_cache: bool = True,
    ) -> Path:
        """
        将文本转换为语音文件并返回文件路径。

        - voice/language 可选；若为空则使用默认配置
        - use_cache 为 True 时，同一文本 + voice 会命中缓存
        """


class ElevenLabsTTSClient(TTSClient):
    """
    ElevenLabs HTTP TTS（v1.4 默认主路径）。

    - 使用 `require_elevenlabs_tts_config` 解析 api_key / voice_id / model / output_format
    - `synthesize(..., voice=...)` 非空时覆盖配置中的 voice_id（便于测试或临时换声）
    """

    def __init__(self, cfg: TTSConfig, settings: Settings) -> None:
        self._cfg = cfg
        self._settings = settings

    def _get_cache_path(self, text: str, el: ElevenLabsConfig, voice_id: str) -> Path:
        output_dir = Path(self._settings.app.output_dir)
        cache_dir = get_tts_cache_dir(output_dir)
        ext = _cache_ext_for_output_format(el.output_format)
        h = hashlib.sha256()
        h.update(voice_id.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        h.update(el.model.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        h.update(el.output_format.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        h.update(text.encode("utf-8", errors="ignore"))
        text_hash = h.hexdigest()[:32]
        # 目录名用 voice_id 便于人工排查；含路径不安全字符时替换
        safe_voice = voice_id.replace("/", "_").replace("\\", "_") or "default"
        return get_tts_cache_file(cache_dir, voice=safe_voice, text_hash=text_hash, ext=ext)

    def _request_tts(self, text: str, el: ElevenLabsConfig, voice_id: str, dest: Path) -> None:
        url = f"{_ELEVENLABS_API_BASE}/v1/text-to-speech/{voice_id}"
        timeout = max(float(self._cfg.timeout_seconds), 5.0)
        headers = {
            "xi-api-key": el.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload: dict[str, str] = {
            "text": text,
            "model_id": el.model,
        }
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
            # ConnectError、TLS、DNS 等均属网络层
            raise TTSServiceError(
                f"ElevenLabs TTS：网络请求失败（{type(exc).__name__}）。详情：{exc!s}"
            ) from exc

        if resp.status_code != 200:
            raise _elevenlabs_http_to_tts_error(resp, voice_id=voice_id, model_id=el.model)

        _verify_audio_response_body(resp.content, el.output_format)

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
        except OSError as exc:
            raise TTSServiceError(
                f"ElevenLabs TTS：音频已返回，但写入缓存文件失败：{dest}。详情：{exc!s}"
            ) from exc

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

        cache_path = self._get_cache_path(text, el, voice_id)
        if use_cache and cache_path.exists():
            logger.debug("命中 TTS 缓存：%s", cache_path)
            return cache_path

        last_error: Optional[BaseException] = None
        max_retries = max(self._cfg.max_retries, 1)
        for attempt in range(1, max_retries + 1):
            try:
                with log_timing(logger, f"tts_elevenlabs_attempt_{attempt}"):
                    self._request_tts(text, el, voice_id, cache_path)
                return cache_path
            except TTSServiceError as exc:
                last_error = exc
                logger.warning("ElevenLabs TTS 调用失败（第 %d 次）：%s", attempt, repr(exc))

        raise TTSServiceError(
            "ElevenLabs TTS：在配置的重试次数内仍未成功。"
            f" 最后一次错误：{last_error!s}"
        ) from last_error


class EdgeTTSSimpleClient(TTSClient):
    """
    基于 edge-tts 的 TTS 实现（备选路径）。

    - 使用 Settings.app.output_dir 下的 cache/tts 作为缓存目录
    - 不做复杂的发音人/语言管理，只透传配置中的 voice
    """

    def __init__(self, cfg: TTSConfig, settings: Settings) -> None:
        self._cfg = cfg
        self._settings = settings

    def _get_cache_path(self, text: str, voice: str) -> Path:
        output_dir = Path(self._settings.app.output_dir)
        cache_dir = get_tts_cache_dir(output_dir)
        h = hashlib.sha256()
        h.update(voice.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        h.update(text.encode("utf-8", errors="ignore"))
        text_hash = h.hexdigest()[:32]
        return get_tts_cache_file(cache_dir, voice=voice, text_hash=text_hash, ext="mp3")

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

        effective_voice = voice or self._cfg.voice
        if not effective_voice:
            raise AIServiceError("TTS voice 未配置。请在 config.yaml 或环境变量中设置 tts.voice。")

        cache_path = self._get_cache_path(text, effective_voice)
        if use_cache and cache_path.exists():
            logger.debug("命中 TTS 缓存：%s", cache_path)
            return cache_path

        last_error: Optional[Exception] = None
        max_retries = max(self._cfg.max_retries, 1)
        for attempt in range(1, max_retries + 1):
            try:
                with log_timing(logger, f"tts_edge_attempt_{attempt}"):
                    asyncio.run(self._synthesize_once_async(text, effective_voice, cache_path))
                return cache_path
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("TTS 调用失败（第 %d 次）：%s", attempt, repr(exc))

        raise AIServiceError("TTS 多次重试后仍然失败。") from last_error


def get_default_tts_client(settings: Settings | None = None) -> TTSClient:
    """
    基于全局 Settings 返回默认 TTSClient 实例。

    - v1.4 默认：provider=elevenlabs → ElevenLabsTTSClient
    - 备选：provider=edge_tts → EdgeTTSSimpleClient
    """
    s = settings or load_settings()
    cfg = s.tts
    prov = _normalize_tts_provider(cfg.provider)
    if prov == "elevenlabs":
        return ElevenLabsTTSClient(cfg, s)
    if prov in ("edge_tts", "edge-tts", "edge"):
        return EdgeTTSSimpleClient(cfg, s)
    raise AIServiceError(f"暂不支持的 TTS provider：{cfg.provider!r}（支持：elevenlabs、edge_tts）")
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from podcast_ai.core.exceptions import ConfigError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class LLMConfig(BaseModel):
    provider: str = "openai_compatible"
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "minimax/minimax-m2.5"
    timeout_seconds: int = 60
    max_retries: int = 2
    # v3.4：None = 仅当 base_url 指向 openrouter.ai 时在请求体附加 response_format；True/False 强制开关
    structured_output: bool | None = Field(
        default=None,
        description="OpenRouter 结构化 JSON：None 自动检测 host；True/False 覆盖",
    )
    # v4.6：OpenRouter 供应方路由（与 llm.provider「客户端实现类型」无关）。留空=请求体不携带 provider，由 OpenRouter 自动选路。
    # 非空：可为 JSON 对象字符串（与官方 provider 字段一致），或单个供应方 slug（实现为 {"only": [slug]}）。
    openrouter_provider: str = ""


def is_openrouter_base_url(url: str) -> bool:
    """base_url 是否指向 OpenRouter 网关（用于附加 response_format / provider 等 OpenRouter 专有字段）。"""
    return "openrouter.ai" in (url or "").strip().lower()


def should_use_structured_output(cfg: LLMConfig) -> bool:
    """
    是否应在 chat/completions 请求中附带 `response_format`（json_schema strict）。

    - `structured_output=True`：始终启用
    - `structured_output=False`：始终关闭（避免非 OpenRouter 网关 400）
    - `None`：仅当 `base_url` 主机名含 `openrouter.ai` 时启用
    """
    if cfg.structured_output is True:
        return True
    if cfg.structured_output is False:
        return False
    return is_openrouter_base_url(cfg.base_url)


class ElevenLabsConfig(BaseModel):
    """
    ElevenLabs TTS 专用字段（v1.4+），供 `infra/tts_client.py` 消费。

    - 环境变量嵌套：`PODCAST_AI_TTS__ELEVENLABS__API_KEY` 等（与 pydantic-settings 约定一致）
    - `model` / `output_format` 提供合理默认值；`api_key` / `voice_id` 须在 provider=elevenlabs 时由调用方校验为非空
    """

    api_key: str = ""
    voice_id: str = ""
    # 官方模型 id 示例：eleven_multilingual_v2、eleven_turbo_v2_5
    model: str = "eleven_multilingual_v2"
    # 输出格式示例：mp3_44100_128（与混音链路兼容的 MP3）；具体取值以 ElevenLabs API 为准
    output_format: str = "mp3_44100_128"


class MiniMaxConfig(BaseModel):
    """MiniMax TTS 专用字段（v4.1）。"""

    api_key: str = ""
    model: str = "speech-2.8-hd"
    # v4.1：先固定默认 voice_setting/audio_setting，仅暴露最小必填 voice_id。
    voice_id: str = "male-qn-qingse"
    # 为后续异步查询接口预留；同步路径暂不使用
    query_interval_ms: int = 500
    query_timeout_seconds: int = 30


class TTSConfig(BaseModel):
    # v4.1 默认主路径为 ElevenLabs；标准值：edge / elevenlabs / minimax
    provider: str = "elevenlabs"
    api_key: str = ""
    voice: str = ""
    elevenlabs: ElevenLabsConfig = Field(default_factory=ElevenLabsConfig)
    minimax: MiniMaxConfig = Field(default_factory=MiniMaxConfig)
    timeout_seconds: int = 60
    max_retries: int = 2

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        raw = str(value or "").strip().lower()
        aliases = {
            "edge_tts": "edge",
            "edge-tts": "edge",
        }
        normalized = aliases.get(raw, raw)
        if normalized not in {"edge", "elevenlabs", "minimax"}:
            raise ValueError(
                "tts.provider 仅支持 edge / elevenlabs / minimax "
                f"（收到：{value!r}）"
            )
        return normalized


class AudioConfig(BaseModel):
    crossfade_seconds: float = 8.0
    loudness_target_lufs: float = -14.0
    # v2.0：串词-歌曲边界 crossfade（默认约 2–4s 区间的中值；置 0 可回退为硬切）
    voice_music_crossfade_seconds: float = 3.0
    # v4.2：开启后对「串词->歌」按下一首 intro 估计动态决定 vm；失败回退默认 vm。
    voice_music_intro_align_enabled: bool = True
    voice_music_intro_align_max_seconds: float = 3.0
    # 混音前是否对每首曲目/每段 TTS 做 simple_normalize（约 -16 dBFS）；false=保留源电平
    per_track_normalize_enabled: bool = True
    # 仅作用于串词：拉到目标平均 dBFS（None=关闭）。与 per_track_normalize 互斥时优先 per_track_normalize。
    voice_normalize_to_dbfs: float | None = None
    # 仅作用于串词：额外固定增益（dB），正数变大；在可选 normalize 之后应用
    voice_gain_db: float = 0.0
    # 串词→歌叠化窗内，音乐相对满电平的增益上限（dB）。0=不限制（旧行为）；建议 -6～-9
    voice_music_overlay_music_max_db: float = 0.0
    # 叠化结束后，音乐从上限电平爬回满电平的时长（秒）；0=关闭；仅当 overlay_max_db<0 时生效
    voice_music_post_overlay_ramp_seconds: float = 0.0


class CacheConfig(BaseModel):
    enabled: bool = True
    dir: str = "./output/cache"


class AppConfig(BaseModel):
    music_dir: str = "./music"
    output_dir: str = "./output"
    # v3.6：multi-agent 路径下按轮次落盘 audit（关闭则不写 audit/，避免 I/O 影响主流程）
    multi_agent_audit_enabled: bool = Field(default=True, description="是否启用多 Agent 审计落盘")
    # v6.0：multi_agent 编排双轨；默认 staged（闸门）；legacy 冻结保留
    orchestration_mode: Literal["staged", "legacy"] = Field(
        default="staged",
        description="multi_agent 编排：staged=分阶段闸门（默认）；legacy=旧全局 Critic 回修",
    )

    @field_validator("orchestration_mode", mode="before")
    @classmethod
    def _normalize_orchestration_mode(cls, value: object) -> str:
        raw = str(value or "").strip().lower()
        if raw not in {"staged", "legacy"}:
            raise ValueError(
                "app.orchestration_mode 仅支持 staged / legacy "
                f"（收到：{value!r}）"
            )
        return raw


class _YamlSettingsSource(PydanticBaseSettingsSource):
    def __init__(
        self,
        settings_cls: type[BaseSettings],
        yaml_path: Path | None,
    ) -> None:
        super().__init__(settings_cls)
        self._yaml_path = yaml_path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        raise NotImplementedError("YAML source does not support per-field loading.")

    def __call__(self) -> dict[str, Any]:
        if self._yaml_path is None or not self._yaml_path.exists():
            return {}
        data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"配置文件格式错误：期望 YAML 顶层为 object：{self._yaml_path}")
        return data


class Settings(BaseSettings):
    """
    配置加载优先级（高 → 低）：
    1) 显式传入的初始化参数
    2) 环境变量（含 .env）
    3) config.yaml（若存在）
    4) 代码默认值
    """

    model_config = SettingsConfigDict(
        env_prefix="PODCAST_AI_",
        env_nested_delimiter="__",
        extra="ignore",
        env_file=".env"
    )

    config_file: str = Field(
        default="",
        description="配置文件路径；为空则默认尝试读取 ./config.yaml",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 先读取 env/.env，拿到 config_file（如有）
        env_first = env_settings()
        dotenv_first = dotenv_settings()
        config_file = (env_first.get("config_file") or dotenv_first.get("config_file") or "").strip()

        yaml_path: Path | None
        if config_file:
            yaml_path = Path(config_file).expanduser()
        else:
            yaml_path = Path("config.yaml")

        yaml_source = _YamlSettingsSource(settings_cls, yaml_path=yaml_path)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


def load_settings(config_file: str | Path | None = None) -> Settings:
    """
    加载配置。

    - **默认行为**：若工作目录存在 `config.yaml` 则读取；否则仅使用环境变量与默认值
    - **显式指定**：传入 `config_file` 可强制使用指定 YAML 文件（仍可被环境变量覆盖单字段）
    """
    if config_file is None:
        return Settings()
    return Settings(config_file=str(config_file))


def require_elevenlabs_tts_config(tts: TTSConfig) -> ElevenLabsConfig:
    """
    在调用 ElevenLabs TTS 前做最小必填校验，并返回可直接用于客户端的配置副本。

    - 仅当 `tts.provider`（不区分大小写）为 `elevenlabs` 时生效；否则抛出 `ConfigError`。
    - `api_key` 优先取 `tts.elevenlabs.api_key`，为空时回退到 `tts.api_key`（便于与既有 LLM 风格 key 共用）。
    - `voice_id` 必须非空；`model` / `output_format` 若为空则使用 `ElevenLabsConfig` 默认值。
    """
    prov = (tts.provider or "").strip().lower()
    if prov != "elevenlabs":
        raise ConfigError(
            f"当前 TTS provider 为 {tts.provider!r}，需要 elevenlabs 才能使用 ElevenLabs 配置。"
            "请在 config.yaml 或环境变量 PODCAST_AI_TTS__PROVIDER 中设置 provider: elevenlabs。"
        )

    el = tts.elevenlabs.model_copy()
    api_key = (el.api_key or tts.api_key or "").strip()
    voice_id = (el.voice_id or "").strip()
    _defaults = ElevenLabsConfig()
    model = (el.model or "").strip() or _defaults.model
    output_format = (el.output_format or "").strip() or _defaults.output_format

    missing: list[str] = []
    if not api_key:
        missing.append(
            "api_key 未设置：请配置 tts.elevenlabs.api_key 或环境变量 PODCAST_AI_TTS__ELEVENLABS__API_KEY 或 tts.api_key"
        )
    if not voice_id:
        missing.append(
            "voice_id 未设置：请配置 tts.elevenlabs.voice_id 或环境变量 PODCAST_AI_TTS__ELEVENLABS__VOICE_ID"
        )

    if missing:
        raise ConfigError("ElevenLabs TTS 配置不完整：\n" + "\n".join(f"  - {m}" for m in missing))

    return ElevenLabsConfig(
        api_key=api_key,
        voice_id=voice_id,
        model=model,
        output_format=output_format,
    )


def require_minimax_tts_config(tts: TTSConfig) -> MiniMaxConfig:
    """
    在调用 MiniMax TTS 前做最小必填校验，并返回可直接用于客户端的配置副本。

    - 仅当 `tts.provider` 为 `minimax` 时生效；否则抛出 `ConfigError`。
    - `api_key` 优先取 `tts.minimax.api_key`，为空时回退到 `tts.api_key`。
    - `model` / `voice_id` 为空时回退到 `MiniMaxConfig` 默认值。
    """
    prov = (tts.provider or "").strip().lower()
    if prov != "minimax":
        raise ConfigError(
            f"当前 TTS provider 为 {tts.provider!r}，需要 minimax 才能使用 MiniMax 配置。"
            "请在 config.yaml 或环境变量 PODCAST_AI_TTS__PROVIDER 中设置 provider: minimax。"
        )

    mm = tts.minimax.model_copy()
    defaults = MiniMaxConfig()
    api_key = (mm.api_key or tts.api_key or "").strip()
    model = (mm.model or "").strip() or defaults.model
    voice_id = (mm.voice_id or tts.voice or "").strip() or defaults.voice_id

    missing: list[str] = []
    if not api_key:
        missing.append(
            "api_key 未设置：请配置 tts.minimax.api_key 或环境变量 PODCAST_AI_TTS__MINIMAX__API_KEY 或 tts.api_key"
        )
    if not voice_id:
        missing.append(
            "voice_id 未设置：请配置 tts.minimax.voice_id 或环境变量 PODCAST_AI_TTS__MINIMAX__VOICE_ID"
        )

    if missing:
        raise ConfigError("MiniMax TTS 配置不完整：\n" + "\n".join(f"  - {m}" for m in missing))

    return MiniMaxConfig(
        api_key=api_key,
        model=model,
        voice_id=voice_id,
        query_interval_ms=mm.query_interval_ms,
        query_timeout_seconds=mm.query_timeout_seconds,
    )


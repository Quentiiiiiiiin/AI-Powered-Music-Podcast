from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

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
    url = (cfg.base_url or "").strip().lower()
    return "openrouter.ai" in url


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


class TTSConfig(BaseModel):
    # v1.4 默认主路径为 ElevenLabs；仍可通过 provider=edge_tts 回退 Edge
    provider: str = "elevenlabs"
    api_key: str = ""
    voice: str = ""
    elevenlabs: ElevenLabsConfig = Field(default_factory=ElevenLabsConfig)
    timeout_seconds: int = 60
    max_retries: int = 2


class AudioConfig(BaseModel):
    crossfade_seconds: float = 8.0
    loudness_target_lufs: float = -14.0
    # v2.0：串词-歌曲边界 crossfade（默认约 2–4s 区间的中值；置 0 可回退为硬切）
    voice_music_crossfade_seconds: float = 3.0


class CacheConfig(BaseModel):
    enabled: bool = True
    dir: str = "./output/cache"


class AppConfig(BaseModel):
    music_dir: str = "./music"
    output_dir: str = "./output"


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


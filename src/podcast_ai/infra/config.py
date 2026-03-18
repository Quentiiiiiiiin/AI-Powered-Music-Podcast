from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class LLMConfig(BaseModel):
    provider: str = "openai_compatible"
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 60
    max_retries: int = 2


class TTSConfig(BaseModel):
    provider: str = "edge_tts"
    api_key: str = ""
    voice: str = ""
    timeout_seconds: int = 60
    max_retries: int = 2


class AudioConfig(BaseModel):
    crossfade_seconds: float = 8.0
    loudness_target_lufs: float = -14.0


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


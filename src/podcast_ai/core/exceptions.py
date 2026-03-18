from __future__ import annotations


class PodcastAIError(Exception):
    """项目内可预期错误的基类（用于向 CLI 提供更友好的提示）。"""


class ConfigError(PodcastAIError):
    """配置文件/环境变量缺失或格式错误。"""


class DependencyError(PodcastAIError):
    """系统依赖缺失（如 FFmpeg 不可用）。"""


class AIServiceError(PodcastAIError):
    """LLM/TTS 等外部 AI 服务调用失败。"""


class AudioProcessingError(PodcastAIError):
    """音频处理/渲染失败。"""


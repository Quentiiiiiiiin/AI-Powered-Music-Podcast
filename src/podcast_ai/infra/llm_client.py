from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.infra.config import LLMConfig, Settings, load_settings

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """LLMClient 抽象接口。"""

    @abstractmethod
    def generate(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        根据对话消息生成文本结果。

        - messages 采用 OpenAI Chat 兼容格式：[{\"role\": \"user\", \"content\": \"...\"}, ...]
        - 返回值为模型生成的完整文本内容
        """


class OpenAICompatibleLLMClient(LLMClient):
    """
    默认实现：面向 OpenAI 兼容 API 的简单封装。

    依赖配置：
      - base_url: 例如 https://api.openai.com/v1
      - model:    模型名称
      - api_key:  Bearer token（不会出现在日志中）
    """

    def __init__(self, cfg: LLMConfig) -> None:
        if not cfg.base_url:
            # 允许在没有 base_url 时实例化，但首次调用会失败并给出清晰错误
            logger.warning("LLM base_url 未配置，调用时将抛出错误。")
        self._cfg = cfg

    def _build_client(self) -> httpx.Client:
        timeout = httpx.Timeout(self._cfg.timeout_seconds)
        headers = {}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        return httpx.Client(base_url=self._cfg.base_url or "", timeout=timeout, headers=headers)

    def generate(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        if not self._cfg.base_url:
            raise AIServiceError("LLM base_url 未配置（llm.base_url 为空）。请在 config.yaml 或环境变量中进行配置。")
        if not self._cfg.model:
            raise AIServiceError("LLM 模型名称未配置（llm.model 为空）。")
         
         # 新增：请求前校验 api_key
        if not self._cfg.api_key or not self._cfg.api_key.strip():
            raise AIServiceError(
            "LLM api_key 未配置。请在 config.yaml 中设置 llm.api_key，"
            "或使用环境变量 PODCAST_AI_LLM__API_KEY。OpenRouter 需有效 API Key 才能调用。"
        )
        
        url = "/chat/completions"
        payload: Dict[str, Any] = {
            "model": self._cfg.model,
            "messages": messages,
        }
        payload.update(kwargs)

        # 日志中打印脱敏后的请求信息（不包含 api_key）
        safe_payload = {**payload}
        try:
            preview = json.dumps(safe_payload, ensure_ascii=False)[:512]
        except Exception:  # noqa: BLE001
            preview = str(safe_payload)[:512]
        logger.debug("LLM request (sanitized): %s", preview)

        last_error: Optional[Exception] = None
        for attempt in range(1, max(self._cfg.max_retries, 1) + 1):
            try:
                with self._build_client() as client, log_timing(logger, f"llm_call_attempt_{attempt}"):
                    resp = client.post(url, json=payload)
                if resp.status_code >= 500:
                    raise AIServiceError(f"LLM 服务端错误（HTTP {resp.status_code}）。")
                if resp.status_code >= 400:
                    raise AIServiceError(f"LLM 请求失败（HTTP {resp.status_code}）：{resp.text[:300]}")

                data = resp.json()
                # OpenAI Chat 兼容字段：choices[0].message.content
                try:
                    content = data["choices"][0]["message"]["content"]
                except Exception as exc:  # noqa: BLE001
                    raise AIServiceError("LLM 响应格式不符合预期。") from exc

                logger.debug("LLM response length=%d chars", len(content))
                return content
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("LLM 调用失败（第 %d 次）：%s", attempt, repr(exc))

        raise AIServiceError("LLM 多次重试后仍然失败。") from last_error


def get_default_llm_client(settings: Settings | None = None) -> LLMClient:
    """
    基于全局 Settings 返回一个默认 LLMClient 实例。

    - 当前仅支持 provider = \"openai_compatible\"
    - 未来可根据 provider 扩展到不同实现
    """
    s = settings or load_settings()
    cfg = s.llm
    if cfg.provider == "openai_compatible":
        return OpenAICompatibleLLMClient(cfg)
    raise AIServiceError(f"暂不支持的 LLM provider：{cfg.provider}")


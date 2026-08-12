from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from podcast_ai.core.exceptions import AIServiceError
from podcast_ai.core.logging_config import log_timing
from podcast_ai.infra.config import LLMConfig, Settings, is_openrouter_base_url, load_settings

logger = logging.getLogger(__name__)


def _openrouter_provider_object_from_config(raw: str) -> dict[str, Any]:
    """
    将 `llm.openrouter_provider` 解析为 OpenRouter `provider` 对象。

    - 以 `{` 开头：按 JSON object 解析（需与 OpenRouter 文档字段一致，如 only/order 等）。
    - 否则：视为单个供应方 slug，实现为 ``{"only": [slug]}``（显式限定路由）。
    """
    s = raw.strip()
    if not s:
        raise ValueError("openrouter_provider 为空")
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"openrouter_provider 不是合法 JSON object：{exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError("openrouter_provider JSON 顶层必须是 object")
        return obj
    return {"only": [s]}


def _summarize_provider_for_log(provider: Any) -> Any:
    """日志脱敏：保留键与大致结构，避免打印过长列表/嵌套。"""
    if not isinstance(provider, dict):
        return "<non-dict>"
    out: dict[str, Any] = {}
    for k, v in provider.items():
        if isinstance(v, (list, tuple)):
            out[k] = f"<list len={len(v)}>"
        elif isinstance(v, dict):
            out[k] = "<dict>"
        else:
            out[k] = v
    return out


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
        if not self._cfg.api_key or not self._cfg.api_key.strip():
            raise AIServiceError(
                "LLM api_key 未配置。请在 config.yaml 中设置 llm.api_key，"
                "或使用环境变量 PODCAST_AI_LLM__API_KEY。OpenRouter 需有效 API Key 才能调用。",
            )

        url = "/chat/completions"
        payload: Dict[str, Any] = {
            "model": self._cfg.model,
            "messages": messages,
        }
        payload.update(kwargs)

        # v4.6：OpenRouter 且配置了供应方路由时写入官方 `provider` 字段（在 kwargs 之后注入，避免被覆盖）。
        if is_openrouter_base_url(self._cfg.base_url) and "provider" not in payload:
            raw_or = (self._cfg.openrouter_provider or "").strip()
            if raw_or:
                try:
                    payload["provider"] = _openrouter_provider_object_from_config(raw_or)
                except ValueError as exc:
                    raise AIServiceError(f"llm.openrouter_provider 配置无效：{exc}") from exc

        # 日志中打印脱敏后的请求信息（不包含 api_key、不展开完整 json_schema）
        safe_payload = dict(payload)
        if "response_format" in safe_payload:
            rf = safe_payload.get("response_format") or {}
            js = rf.get("json_schema") if isinstance(rf, dict) else {}
            safe_payload["response_format"] = {
                "type": rf.get("type") if isinstance(rf, dict) else None,
                "json_schema": {
                    "name": js.get("name") if isinstance(js, dict) else None,
                    "strict": js.get("strict") if isinstance(js, dict) else None,
                    "schema": "<omitted>",
                },
            }
        if "provider" in safe_payload:
            safe_payload["provider"] = _summarize_provider_for_log(safe_payload["provider"])
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
                    body_preview = (resp.text or "")[:300]
                    hint = ""
                    if resp.status_code == 400:
                        low = body_preview.lower()
                        if "response_format" in low or "json_schema" in low:
                            hint = "（可能与 response_format/结构化输出不被当前网关或模型支持有关）"
                        elif "provider" in low or "routing" in low:
                            hint = "（可能与 OpenRouter provider 路由与 model 不兼容或 slug 非法有关；请核对 llm.openrouter_provider 与官方文档）"
                    raise AIServiceError(f"LLM 请求失败（HTTP {resp.status_code}）：{body_preview}{hint}")

                data = resp.json()
                # OpenAI Chat 兼容字段：choices[0].message.content
                try:
                    content = data["choices"][0]["message"]["content"]
                except Exception as exc:  # noqa: BLE001
                    raise AIServiceError("LLM 响应格式不符合预期。") from exc

                logger.debug("LLM response length=%d chars", len(content))
                return content
            except AIServiceError:
                raise
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


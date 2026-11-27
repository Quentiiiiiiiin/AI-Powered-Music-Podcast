"""v7.2：Agent 输出校验重试（generate → parse → sanitize），与 HTTP retry 分计。

`extra_retries` = 额外 generate 次数（不含首次）；0 表示只尝试一次。
`generate` 抛出的 `AIServiceError` 视为调用/HTTP 失败，直接上抛，不消耗本层次数。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from podcast_ai.core.exceptions import AIServiceError

logger = logging.getLogger(__name__)

TSanitized = TypeVar("TSanitized")


@dataclass(frozen=True)
class AgentOutputResult:
    raw: str
    parsed: dict[str, Any]
    sanitized: Any


def _as_ai_error(agent_label: str, stage: str, exc: BaseException) -> AIServiceError:
    if isinstance(exc, AIServiceError):
        return exc
    err = AIServiceError(f"{agent_label} {stage} 失败：{exc!r}")
    err.__cause__ = exc
    return err


def run_generate_parse_sanitize(
    *,
    agent_label: str,
    extra_retries: int,
    generate: Callable[[], str],
    parse: Callable[[str], dict[str, Any]],
    sanitize: Callable[[dict[str, Any]], TSanitized],
    on_attempt_failed: Callable[[str, str, BaseException], None] | None = None,
) -> AgentOutputResult:
    """
    同一 messages/kwargs 下循环 generate→parse→sanitize。

    成功返回 parsed + sanitized；耗尽抛最后一次 parse/sanitize 的 AIServiceError。
    """
    extra = 0 if extra_retries < 0 else int(extra_retries)
    max_attempts = extra + 1
    last_error: AIServiceError | None = None
    last_stage = "parse"

    for attempt in range(1, max_attempts + 1):
        try:
            raw = generate()
        except AIServiceError:
            raise

        try:
            parsed = parse(raw)
        except Exception as exc:  # noqa: BLE001
            last_stage = "parse"
            last_error = _as_ai_error(agent_label, last_stage, exc)
            logger.warning(
                "%s output validation failed attempt=%d/%d stage=parse: %s",
                agent_label,
                attempt,
                max_attempts,
                last_error,
            )
            if on_attempt_failed is not None:
                try:
                    on_attempt_failed(raw, last_stage, last_error)
                except Exception as audit_exc:  # noqa: BLE001
                    logger.error(
                        "%s on_attempt_failed 失败（不掩盖 parse 错误）：%s",
                        agent_label,
                        audit_exc,
                    )
            if attempt >= max_attempts:
                logger.error(
                    "%s output validation exhausted attempts=%d last_stage=parse: %s",
                    agent_label,
                    max_attempts,
                    last_error,
                )
                raise last_error
            continue

        try:
            sanitized = sanitize(parsed)
        except Exception as exc:  # noqa: BLE001
            last_stage = "sanitize"
            last_error = _as_ai_error(agent_label, last_stage, exc)
            logger.warning(
                "%s output validation failed attempt=%d/%d stage=sanitize: %s",
                agent_label,
                attempt,
                max_attempts,
                last_error,
            )
            if on_attempt_failed is not None:
                try:
                    on_attempt_failed(raw, last_stage, last_error)
                except Exception as audit_exc:  # noqa: BLE001
                    logger.error(
                        "%s on_attempt_failed 失败（不掩盖 sanitize 错误）：%s",
                        agent_label,
                        audit_exc,
                    )
            if attempt >= max_attempts:
                logger.error(
                    "%s output validation exhausted attempts=%d last_stage=sanitize: %s",
                    agent_label,
                    max_attempts,
                    last_error,
                )
                raise last_error
            continue

        logger.info(
            "%s output validation succeeded attempt=%d/%d",
            agent_label,
            attempt,
            max_attempts,
        )
        return AgentOutputResult(raw=raw, parsed=parsed, sanitized=sanitized)

    raise last_error or AIServiceError(f"{agent_label} 输出校验重试失败。")

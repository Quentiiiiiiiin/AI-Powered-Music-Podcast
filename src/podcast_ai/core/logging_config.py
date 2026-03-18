from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


_DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _normalize_level(level: str | int | None) -> int:
    if level is None:
        return logging.INFO
    if isinstance(level, int):
        return level
    value = level.strip().upper()
    return logging._nameToLevel.get(value, logging.INFO)


def configure_logging(
    *,
    level: str | int | None = None,
    log_file: str | Path | None = None,
    console: bool = True,
) -> None:
    """
    统一配置 logging。

    - 默认输出到控制台
    - 可选输出到文件（追加写入，UTF-8）
    - 若未显式传入 level，则读取环境变量 `PODCAST_AI_LOG_LEVEL`，缺省为 INFO
    """
    effective_level = _normalize_level(level or os.getenv("PODCAST_AI_LOG_LEVEL"))

    root = logging.getLogger()
    root.setLevel(effective_level)

    # 避免重复添加 handler（常见于 CLI 子命令多次初始化）
    if getattr(root, "_podcast_ai_configured", False):
        return

    formatter = logging.Formatter(_DEFAULT_LOG_FORMAT, datefmt=_DEFAULT_DATE_FORMAT)

    if console:
        sh = logging.StreamHandler()
        sh.setLevel(effective_level)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setLevel(effective_level)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    root._podcast_ai_configured = True  # type: ignore[attr-defined]


@dataclass(frozen=True)
class TimerResult:
    name: str
    elapsed_ms: int


@contextmanager
def log_timing(logger: logging.Logger, name: str, level: int = logging.INFO) -> Iterator[TimerResult]:
    """
    记录某段逻辑耗时的辅助工具。

    用法：
      with log_timing(logger, "scan_library") as t:
          ...
      # 会自动输出一条 "scan_library took 123ms"
    """
    start = time.perf_counter()
    try:
        yield TimerResult(name=name, elapsed_ms=0)
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.log(level, "%s took %dms", name, elapsed_ms)


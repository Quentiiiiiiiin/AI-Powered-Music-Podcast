"""调用项目内嵌的 ncmdump，将网易云 .ncm 批量解码为 mp3/flac。"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from podcast_ai.core.exceptions import DependencyError, PodcastAIError

logger = logging.getLogger(__name__)

_NCM_SUFFIX = ".ncm"


@dataclass(frozen=True)
class NcmDecodeResult:
    """一次目录批量解码的摘要。"""

    source_dir: Path
    output_dir: Path
    ncm_count: int
    binary: Path
    stdout: str
    stderr: str
    cleared_source_files: int = 0


def project_root() -> Path:
    """
    仓库根目录（含 ``tools/``）。

    ``infra/ncmdump.py`` → ``src/podcast_ai/infra`` → 上溯三级到仓库根。
    """
    return Path(__file__).resolve().parents[3]


def resolve_ncmdump_binary(*, explicit: Path | str | None = None) -> Path:
    """
    解析 ncmdump 可执行文件路径。

    优先级：
    1. 显式传入路径
    2. 环境变量 ``PODCAST_AI_NCMDUMP``
    3. 仓库内 ``tools/ncmdump/ncmdump(.exe)``
    4. 系统 PATH 中的 ``ncmdump``
    """
    candidates: list[Path] = []
    if explicit is not None and str(explicit).strip():
        candidates.append(Path(str(explicit)).expanduser())
    env = (os.environ.get("PODCAST_AI_NCMDUMP") or "").strip()
    if env:
        candidates.append(Path(env).expanduser())

    tools_dir = project_root() / "tools" / "ncmdump"
    if platform.system().lower().startswith("win"):
        candidates.append(tools_dir / "ncmdump.exe")
    candidates.append(tools_dir / "ncmdump")

    which = shutil.which("ncmdump")
    if which:
        candidates.append(Path(which))

    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved.is_file():
            return resolved

    raise DependencyError(
        "未找到 ncmdump 可执行文件。请确认仓库内存在 "
        "`tools/ncmdump/ncmdump.exe`（Windows）或设置环境变量 "
        "PODCAST_AI_NCMDUMP 指向可执行文件。"
    )


def list_ncm_files(source_dir: Path, *, recursive: bool = False) -> list[Path]:
    """列出源目录下的 .ncm 文件（默认不递归）。"""
    root = source_dir.expanduser().resolve()
    if not root.is_dir():
        raise PodcastAIError(f"网易云原版音乐目录不存在或不是文件夹：{root}")
    pattern = "**/*" if recursive else "*"
    files = sorted(
        p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() == _NCM_SUFFIX
    )
    return files


def clear_directory_files(directory: Path | str, *, recursive: bool = False) -> int:
    """
    删除目录内文件（默认仅顶层；``recursive=True`` 时含子目录文件）。

    不删除目录本身；子文件夹空壳会保留。
    """
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise PodcastAIError(f"无法清空：目录不存在或不是文件夹：{root}")

    pattern = "**/*" if recursive else "*"
    files = [p for p in root.glob(pattern) if p.is_file()]
    # 深层优先，避免个别平台上对仍被占用路径的奇怪行为
    files.sort(key=lambda p: len(p.parts), reverse=True)

    deleted = 0
    errors: list[str] = []
    for path in files:
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")

    if errors:
        preview = "; ".join(errors[:5])
        more = f" 等 {len(errors)} 项" if len(errors) > 5 else ""
        raise PodcastAIError(
            f"解码已成功，但清空原版目录失败（已删 {deleted} 个）：{preview}{more}"
        )
    return deleted


def decode_ncm_directory(
    source_dir: Path | str,
    output_dir: Path | str,
    *,
    recursive: bool = False,
    binary: Path | str | None = None,
    timeout_seconds: float | None = 600.0,
    clear_source_after: bool = True,
) -> NcmDecodeResult:
    """
    对 ``source_dir`` 下全部 .ncm 执行解码，输出到 ``output_dir``。

    等价命令（非递归）：
      ``ncmdump -d source_dir -o output_dir``

    默认在解码成功后清空原版目录内文件（与 ``recursive`` 扫描范围一致）。
    原版目录与输出目录不可为同一路径，否则会误删解码结果。
    """
    src = Path(source_dir).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    if not src.is_dir():
        raise PodcastAIError(f"网易云原版音乐目录不存在或不是文件夹：{src}")
    if clear_source_after and (src == out or src in out.parents):
        raise PodcastAIError(
            "解码输出目录不能位于原版目录内（或与之相同）："
            "解码成功后会清空原版目录文件，可能误删转码结果。"
            f" source={src} output={out}"
        )

    ncm_files = list_ncm_files(src, recursive=recursive)
    if not ncm_files:
        raise PodcastAIError(
            f"源目录中未找到 .ncm 文件：{src}"
            + ("（已开启递归）" if recursive else "（仅扫描顶层，不含子文件夹）")
        )

    exe = resolve_ncmdump_binary(explicit=binary)
    out.mkdir(parents=True, exist_ok=True)

    cmd = [str(exe), "-d", str(src), "-o", str(out)]
    if recursive:
        cmd.append("-r")

    logger.info(
        "ncmdump decode: binary=%s source=%s output=%s ncm_count=%s recursive=%s",
        exe,
        src,
        out,
        len(ncm_files),
        recursive,
    )
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DependencyError(f"无法启动 ncmdump：{exe}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PodcastAIError(
            f"ncmdump 执行超时（>{timeout_seconds}s）：source={src}"
        ) from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout or f"exit={completed.returncode}"
        raise PodcastAIError(
            f"ncmdump 解码失败（exit={completed.returncode}）：{detail}"
        )

    if stdout:
        logger.info("ncmdump stdout:\n%s", stdout)
    if stderr:
        logger.warning("ncmdump stderr:\n%s", stderr)

    cleared = 0
    if clear_source_after:
        cleared = clear_directory_files(src, recursive=recursive)
        logger.info("cleared %s file(s) from source dir %s", cleared, src)

    return NcmDecodeResult(
        source_dir=src,
        output_dir=out,
        ncm_count=len(ncm_files),
        binary=exe,
        stdout=stdout,
        stderr=stderr,
        cleared_source_files=cleared,
    )

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

from pydub import AudioSegment  # type: ignore[import-untyped]

from podcast_ai.core.exceptions import AudioProcessingError, DependencyError

logger = logging.getLogger(__name__)


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def is_ffmpeg_available() -> bool:
    """检测 ffmpeg 是否可用。"""
    return _which("ffmpeg") is not None


def is_ffprobe_available() -> bool:
    """检测 ffprobe 是否可用。"""
    return _which("ffprobe") is not None


def ensure_ffmpeg_available() -> None:
    """若 ffmpeg/ffprobe 不可用，则抛出 DependencyError。"""
    if not is_ffmpeg_available() or not is_ffprobe_available():
        raise DependencyError(
            "未检测到可用的 FFmpeg/ffprobe，请先安装并加入系统 PATH。\n"
            "示例（Windows）：\n"
            "  1) 从 https://ffmpeg.org/ 下载或使用包管理器安装\n"
            "  2) 将 ffmpeg/bin 目录添加到环境变量 PATH\n"
        )


def load_audio(path: Path) -> AudioSegment:
    """加载音频文件为 AudioSegment。"""
    ensure_ffmpeg_available()
    try:
        return AudioSegment.from_file(path)
    except Exception as exc:  # noqa: BLE001
        raise AudioProcessingError(f"加载音频失败：{path}") from exc


def get_duration_seconds(path: Path) -> float:
    """获取音频时长（秒）。"""
    audio = load_audio(path)
    return audio.duration_seconds


def estimate_bpm(path: Path, max_seconds: float | None = 30.0) -> float | None:
    """
    使用 librosa 估计 BPM（可选只分析前 max_seconds 秒以提速）。
    失败或无法估计时返回 None。
    """
    try:
        import librosa  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("librosa 未安装，跳过 BPM 估计。")
        return None
    try:
        y, sr = librosa.load(path, duration=max_seconds, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        if isinstance(tempo, (list, tuple)):
            tempo = tempo[0] if tempo else 0.0
        return float(tempo) if tempo and tempo > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("BPM 估计失败 %s: %s", path, exc)
        return None


def export_audio(
    audio: AudioSegment,
    dest: Path,
    format: str | None = None,
) -> None:
    """
    导出 AudioSegment 为指定格式文件。

    - 若未指定 format，则根据目标文件扩展名推断
    """
    ensure_ffmpeg_available()
    fmt = format or dest.suffix.lstrip(".") or "mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        audio.export(dest, format=fmt)
    except Exception as exc:  # noqa: BLE001
        raise AudioProcessingError(f"导出音频失败：{dest}") from exc


def simple_normalize(audio: AudioSegment, target_dbfs: float = -20.0) -> AudioSegment:
    """
    简单音量归一化：将整体电平移动到 target_dbfs。
    （MVP 级别，后续由 loudnorm 做精细响度标准化）
    """
    change = target_dbfs - audio.dBFS
    return audio.apply_gain(change)


def crossfade_concat(
    segments: Iterable[AudioSegment],
    crossfade_seconds: float,
) -> AudioSegment:
    """
    将多段音频按固定 crossfade 秒数拼接。

    - crossfade_seconds 为 0 或负数时，退化为简单串接
    """
    seg_list: List[AudioSegment] = list(segments)
    if not seg_list:
        raise AudioProcessingError("crossfade_concat 需要至少一段音频。")

    if crossfade_seconds <= 0:
        out = seg_list[0]
        for seg in seg_list[1:]:
            out += seg
        return out

    cf_ms = int(crossfade_seconds * 1000)
    out = seg_list[0]
    for seg in seg_list[1:]:
        out = out.append(seg, crossfade=cf_ms)
    return out


def loudness_normalize_ffmpeg(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -14.0,
    bitrate: str | None = None,
) -> None:
    """
    使用 ffmpeg loudnorm 滤镜做整体响度标准化。

    - bitrate 可选，用于 mp3 等有损编码（如 "192k"）
    - 这是一个包裹 ffmpeg 命令的 MVP 实现，后续可分两遍扫描提高精度
    """
    ensure_ffmpeg_available()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-af",
        f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11",
    ]
    if bitrate:
        cmd.extend(["-b:a", bitrate])
    cmd.append(str(output_path))
    logger.debug("Running ffmpeg loudnorm: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise AudioProcessingError("调用 ffmpeg 失败（无法启动进程）。") from exc

    if proc.returncode != 0:
        logger.error("ffmpeg loudnorm stderr:\n%s", proc.stderr)
        raise AudioProcessingError(
            f"ffmpeg loudnorm 处理失败，返回码 {proc.returncode}。请检查输入文件与 FFmpeg 安装。",
        )


def convert_format(
    input_path: Path,
    output_path: Path,
    extra_args: Tuple[str, ...] | None = None,
) -> None:
    """
    使用 ffmpeg 做格式转换的简单封装。

    - extra_args 可用于传递比特率等附加参数（如 ('-b:a', '192k')）
    """
    ensure_ffmpeg_available()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = ["ffmpeg", "-y", "-i", str(input_path)]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(str(output_path))

    logger.debug("Running ffmpeg convert: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise AudioProcessingError("调用 ffmpeg 失败（无法启动进程）。") from exc

    if proc.returncode != 0:
        logger.error("ffmpeg convert stderr:\n%s", proc.stderr)
        raise AudioProcessingError(
            f"ffmpeg 转码失败，返回码 {proc.returncode}。请检查输入文件与 FFmpeg 安装。",
        )


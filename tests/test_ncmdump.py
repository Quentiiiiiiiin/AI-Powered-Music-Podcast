"""ncmdump 封装与 Console 解码入口。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_ai.console.runner import ConsoleParams, run_ncm_decode
from podcast_ai.core.exceptions import DependencyError, PodcastAIError
from podcast_ai.infra import ncmdump as ncmdump_mod


def test_resolve_ncmdump_binary_prefers_vendored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "repo"
    tools = fake_root / "tools" / "ncmdump"
    tools.mkdir(parents=True)
    # Windows 与非 Windows：两种文件名都放好，resolve 按平台优先命中
    (tools / "ncmdump.exe").write_bytes(b"MZ")
    (tools / "ncmdump").write_bytes(b"#!/bin/sh\n")

    monkeypatch.delenv("PODCAST_AI_NCMDUMP", raising=False)
    monkeypatch.setattr(ncmdump_mod, "project_root", lambda: fake_root)
    monkeypatch.setattr(ncmdump_mod.shutil, "which", lambda _name: None)

    resolved = ncmdump_mod.resolve_ncmdump_binary()
    assert resolved.is_file()
    assert resolved.parent == tools


def test_resolve_ncmdump_binary_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom_ncmdump.exe"
    custom.write_bytes(b"MZ")
    monkeypatch.setenv("PODCAST_AI_NCMDUMP", str(custom))
    monkeypatch.setattr(ncmdump_mod.shutil, "which", lambda _name: None)
    assert ncmdump_mod.resolve_ncmdump_binary() == custom.resolve()


def test_list_ncm_files_non_recursive(tmp_path: Path) -> None:
    (tmp_path / "a.ncm").write_bytes(b"x")
    (tmp_path / "b.mp3").write_bytes(b"y")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "c.ncm").write_bytes(b"z")

    files = ncmdump_mod.list_ncm_files(tmp_path, recursive=False)
    assert [p.name for p in files] == ["a.ncm"]

    files_r = ncmdump_mod.list_ncm_files(tmp_path, recursive=True)
    assert {p.name for p in files_r} == {"a.ncm", "c.ncm"}


def test_decode_ncm_directory_missing_ncm(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    with pytest.raises(PodcastAIError, match="未找到 .ncm"):
        ncmdump_mod.decode_ncm_directory(src, out)


def test_decode_ncm_directory_invokes_cli_and_clears_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    ncm = src / "song.ncm"
    extra = src / "readme.txt"
    ncm.write_bytes(b"ncm")
    extra.write_text("keep-not", encoding="utf-8")
    fake_bin = tmp_path / "ncmdump.exe"
    fake_bin.write_bytes(b"MZ")

    monkeypatch.setattr(ncmdump_mod, "resolve_ncmdump_binary", lambda explicit=None: fake_bin)

    completed = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch.object(ncmdump_mod.subprocess, "run", return_value=completed) as run_mock:
        result = ncmdump_mod.decode_ncm_directory(src, out)

    assert result.ncm_count == 1
    assert result.cleared_source_files == 2
    assert result.output_dir == out.resolve()
    assert out.is_dir()
    assert not ncm.exists()
    assert not extra.exists()
    assert list(src.iterdir()) == []
    cmd = run_mock.call_args.args[0]
    assert cmd[0] == str(fake_bin)
    assert cmd[1:5] == ["-d", str(src.resolve()), "-o", str(out.resolve())]


def test_decode_ncm_directory_rejects_output_inside_source(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "song.ncm").write_bytes(b"ncm")
    with pytest.raises(PodcastAIError, match="不能位于原版目录"):
        ncmdump_mod.decode_ncm_directory(src, src / "out")


def test_decode_ncm_directory_nonzero_exit_keeps_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    ncm = src / "song.ncm"
    ncm.write_bytes(b"ncm")
    fake_bin = tmp_path / "ncmdump.exe"
    fake_bin.write_bytes(b"MZ")
    monkeypatch.setattr(ncmdump_mod, "resolve_ncmdump_binary", lambda explicit=None: fake_bin)

    completed = MagicMock(returncode=2, stdout="", stderr="boom")
    with patch.object(ncmdump_mod.subprocess, "run", return_value=completed):
        with pytest.raises(PodcastAIError, match="解码失败"):
            ncmdump_mod.decode_ncm_directory(src, out)
    assert ncm.exists()


def test_run_ncm_decode_console_success(tmp_path: Path) -> None:
    src = tmp_path / "VipSongs"
    out = tmp_path / "decoded"
    src.mkdir()
    (src / "a.ncm").write_bytes(b"ncm")

    fake = ncmdump_mod.NcmDecodeResult(
        source_dir=src,
        output_dir=out,
        ncm_count=1,
        binary=tmp_path / "ncmdump.exe",
        stdout="",
        stderr="",
    )
    with patch("podcast_ai.infra.ncmdump.decode_ncm_directory", return_value=fake):
        result = run_ncm_decode(
            ConsoleParams(ncm_source_dir=str(src), ncm_output_dir=str(out))
        )

    assert result.status == "success"
    assert result.command == "ncm_decode"
    assert result.artifacts["ncm_output_dir"] == str(out)


def test_run_ncm_decode_requires_source() -> None:
    result = run_ncm_decode(ConsoleParams(ncm_source_dir="", ncm_output_dir="x"))
    assert result.status == "error"
    assert "原版音乐目录" in result.error


def test_vendored_binary_exists_on_windows() -> None:
    """开发机仓库应内嵌 Windows 发行包（非 CI 跨平台强制）。"""
    try:
        binary = ncmdump_mod.resolve_ncmdump_binary()
    except DependencyError:
        pytest.skip("本机未嵌入/未配置 ncmdump")
    assert binary.is_file()

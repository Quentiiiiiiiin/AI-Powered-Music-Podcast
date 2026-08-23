"""Gradio Developer Console：本地调试入口，非正式产品 UI。"""

from __future__ import annotations

__all__ = ["launch_console", "ConsoleLaunchError"]


def launch_console(*, host: str = "127.0.0.1", port: int = 7860, inbrowser: bool = True) -> None:
    """启动本机控制台。延迟导入 Gradio，避免未安装时污染其它 CLI 命令。"""
    from podcast_ai.console.app import launch_console as _launch

    _launch(host=host, port=port, inbrowser=inbrowser)


def __getattr__(name: str):
    if name == "ConsoleLaunchError":
        from podcast_ai.console.app import ConsoleLaunchError

        return ConsoleLaunchError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

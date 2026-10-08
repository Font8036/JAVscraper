"""入口：不带参数启动图形界面，带参数走命令行。

    python -m av_scraper                    图形界面
    python -m av_scraper scan D:\\videos     命令行（见 cli.py）
    python -m av_scraper --help             命令行用法

这样既保住"双击就用"的老路径，又让同一个包能在终端里批量跑。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


def _enable_dpi_awareness() -> None:
    """在创建 Tk 之前声明进程 DPI 感知。

    - 未声明：Windows 把整个窗口从逻辑像素拉伸到物理像素，字体和图标都模糊
    - 声明后：tkinter 直接用物理像素渲染，字体和图标都清晰

    注意：必须在 `tk.Tk()` 之前调用，否则无效。
    """
    if sys.platform != "win32":
        return

    try:
        # Windows 8.1+：Per-Monitor DPI aware（多显示器各自缩放）
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        # Windows Vista+：System DPI aware（跟主显示器缩放）
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def launch_gui() -> None:
    """启动图形界面。Tk 相关的东西都在这条路径上，命令行不会碰。"""
    from .gui.app import App
    from .paths import config_path

    App(config_path()).mainloop()


def main(argv: Sequence[str] | None = None) -> int:
    """返回进程退出码：0 成功，非 0 见 cli.py 的 EXIT_* 。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # 双击 exe / python run.py：还是老的图形界面
        _enable_dpi_awareness()
        launch_gui()
        return 0

    from .cli import run
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())

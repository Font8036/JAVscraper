"""GUI 入口：python -m av_scraper"""

from __future__ import annotations

import sys


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


def main() -> None:
    _enable_dpi_awareness()

    from .gui.app import App
    from .paths import config_path

    App(config_path()).mainloop()


if __name__ == "__main__":
    main()
"""GUI 通用工具。"""

from __future__ import annotations

import logging
import queue
import sys
import tkinter as tk
from pathlib import Path


class QueueLogHandler(logging.Handler):
    """把日志记录推入 queue，由主线程消费后写入 Text 控件。"""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put(self.format(record))
        except Exception:
            self.handleError(record)


def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def set_window_icon(window: tk.Tk | tk.Toplevel) -> None:
    """给任意 Toplevel / Tk 窗口设置应用图标。

    打包后从 _MEIPASS 里找；源码运行从项目根的 assets 里找。
    任何异常都静默忽略——图标设置失败不应影响程序运行。
    """
    try:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            icon = base / "assets" / "icon.ico"
        else:
            # common.py -> gui/ -> av_scraper/ -> 项目根
            icon = (Path(__file__).resolve().parent.parent.parent
                    / "assets" / "icon.ico")

        if icon.exists():
            window.iconbitmap(str(icon))
    except Exception:
        pass

def compute_ui_scale(window: tk.Misc, base_scale: float) -> float:
    """计算实际的 tk scaling 值 = 屏幕 DPI / 72 × 用户缩放系数。

    在声明过 DPI 感知的进程里，winfo_fpixels("1i") 返回真实物理 DPI：
    - 100% 系统缩放 + 96 DPI  → 96
    - 200% 系统缩放 + 192 DPI → 192
    """
    try:
        dpi = window.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    return dpi / 72.0 * base_scale


def apply_ui_scaling(window: tk.Misc, base_scale: float) -> None:
    """设置窗口的 tk scaling。base_scale=1.0 表示 100%。"""
    try:
        window.tk.call("tk", "scaling", compute_ui_scale(window, base_scale))
    except Exception:
        pass


def scale_size(window: tk.Misc, base_scale: float,
               w: int, h: int) -> tuple[int, int]:
    """按 DPI × 用户缩放系数换算窗口尺寸。"""
    try:
        dpi = window.winfo_fpixels("1i")
    except Exception:
        dpi = 96.0
    factor = dpi / 96.0 * base_scale
    return int(w * factor), int(h * factor)

def centered_geometry(window: tk.Misc, w: int, h: int) -> str:
    """返回把 w×h 的窗口居中到屏幕的 geometry 字符串。

    屏幕尺寸和窗口尺寸单位一致（都按物理像素算），
    因为进程已在 __main__.py 里声明过 DPI 感知。
    窗口比屏幕大时退化为左上角对齐，不会算出负坐标。
    """
    try:
        sw = window.winfo_screenwidth()
        sh = window.winfo_screenheight()
    except Exception:
        return f"{w}x{h}"
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    return f"{w}x{h}+{x}+{y}"

def center_and_show(
    window: tk.Tk | tk.Toplevel,
    parent: tk.Misc,
    *,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """在 withdraw 状态下完成布局与居中，然后一次性显示。

    - width/height 显式指定时用指定值；未指定则用内容请求尺寸（reqwidth）。
    - 先 update_idletasks 让 tkinter 计算布局，之后才设置 geometry。
    - 最后才 deiconify —— 用户看到的第一帧就是最终位置，无闪现。
    """
    try:
        window.update_idletasks()
        w = width if width is not None else window.winfo_reqwidth()
        h = height if height is not None else window.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        window.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass
    window.deiconify()

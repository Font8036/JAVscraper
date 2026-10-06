"""GUI 通用工具。"""

from __future__ import annotations
import sys
from pathlib import Path
import logging
import queue
import tkinter as tk

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
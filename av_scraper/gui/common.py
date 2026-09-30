"""GUI 通用工具。"""

from __future__ import annotations

import logging
import queue
import tkinter.ttk as ttk

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


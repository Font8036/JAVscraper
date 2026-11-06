import time
import tkinter as tk
from typing import Optional

class Stopwatch:
    def __init__(self, root: tk.Misc, target_var: tk.StringVar, prefix: str = ""):
        self.root = root
        self.target_var = target_var
        self.prefix = prefix
        
        self._start_time: float = 0.0
        self._running = False
        self._after_id: Optional[str] = None

    def start(self, prefix: str = "") -> None:
        # 防止意外重入导致多个定时器并发
        if self._after_id:
            self.root.after_cancel(self._after_id)
            self._after_id = None
            
        if prefix:
            self.prefix = prefix
            
        self._start_time = time.perf_counter()
        self._running = True
        self._update_loop()

    def set_prefix(self, prefix: str) -> None:
        self.prefix = prefix
        if self._running and self._start_time:
            self._tick()

    def stop(self) -> float:
        self._running = False
        if self._after_id:
            self.root.after_cancel(self._after_id)
            self._after_id = None
            
        elapsed = time.perf_counter() - self._start_time if self._start_time else 0.0
        return elapsed

    def _update_loop(self) -> None:
        if not self._running:
            return
        self._tick()
        self._after_id = self.root.after(200, self._update_loop)

    def _tick(self) -> None:
        elapsed = time.perf_counter() - self._start_time
        # 建议：界面跳动时用紧凑格式，宽度稳定不抖动
        time_str = self.fmt_compact(elapsed)
        
        if self.prefix:
            self.target_var.set(f"{self.prefix}  ({time_str})")
        else:
            self.target_var.set(time_str)

    @staticmethod
    def fmt_compact(seconds: float) -> str:
        """紧凑格式，用于 UI 进度显示（类似秒表）。"""
        total_seconds = int(seconds)
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def fmt_readable(seconds: float) -> str:
        """智能格式化，用于最终结果摘要。"""
        if seconds < 1.0:
            return f"{seconds:.3f} 秒"
        if seconds < 60.0:
            return f"{seconds:.1f} 秒"
            
        total_seconds = int(seconds)
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
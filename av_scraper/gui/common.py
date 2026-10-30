"""GUI 通用工具。"""

from __future__ import annotations

import logging
import queue
import sys
import tkinter as tk
from pathlib import Path
from typing import Any

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

def restore_window_state(
    window: tk.Tk | tk.Toplevel,
    window_id: str,
    app_config: Any,
    default_w: int,
    default_h: int,
) -> None:
    """恢复窗口的位置、尺寸和最大化状态，并处理多显示器越界。"""
    state = app_config.window_geometries.get(window_id, {})
    w = state.get("width", default_w)
    h = state.get("height", default_h)
    x = state.get("x", None)
    y = state.get("y", None)
    zoomed = state.get("zoomed", False)

    # 多显示器边界校验
    if x is not None and y is not None:
        try:
            # vroot 获取的是所有显示器组合起来的虚拟桌面范围
            vx = window.winfo_vrootx()
            vy = window.winfo_vrooty()
            vw = window.winfo_vrootwidth()
            vh = window.winfo_vrootheight()
            
            # 如果坐标完全在屏幕外（比如拔了副屏），则丢弃坐标，只恢复尺寸
            if not (vx <= x < vx + vw - 50 and vy <= y < vy + vh - 50):
                x = y = None
        except Exception:
            x = y = None

    if x is not None and y is not None:
        window.geometry(f"{w}x{h}+{x}+{y}")
    else:
        window.geometry(f"{w}x{h}")

    if zoomed:
        # 如果之前是最大化，需要延迟一点执行，等窗口映射后再 zoom
        window.after(10, lambda: window.state("zoomed"))


def save_window_state(
    window: tk.Tk | tk.Toplevel,
    window_id: str,
    app_config: Any,
) -> None:
    """保存窗口的尺寸、位置和最大化状态到配置对象。"""
    try:
        state = window.state()
        zoomed = state == "zoomed"
        
        # 获取几何信息（如果是最大化，这会返回全屏尺寸，但我们可以用 normal 状态下的记录）
        # 实际上 Tk 中 zoomed 状态下 winfo_width 也是全屏的。
        # 我们获取正常状态下的尺寸，如果是最大化，需要临时切回 normal 拿尺寸吗？
        # 更好的办法：读取当前的 x/y/w/h，如果最大化，保留 w/h 为上次的 normal 尺寸。
        
        # Tkinter 的 geometry() 在最大化时通常返回类似 "1920x1080+0+0"
        geo = window.geometry()
        # 解析 "WxH+X+Y"
        import re
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geo)
        
        existing = app_config.window_geometries.get(window_id, {})
        
        if m:
            w, h, x, y = map(int, m.groups())
            if zoomed:
                # 如果当前是最大化，保留之前记录的尺寸（如果有），否则保留当前尺寸
                w = existing.get("width", w)
                h = existing.get("height", h)
                x = existing.get("x", x)
                y = existing.get("y", y)
        else:
            w = window.winfo_width()
            h = window.winfo_height()
            x = window.winfo_x()
            y = window.winfo_y()
            if zoomed:
                w = existing.get("width", w)
                h = existing.get("height", h)
                x = existing.get("x", x)
                y = existing.get("y", y)

        app_config.window_geometries[window_id] = {
            "width": w, "height": h, "x": x, "y": y, "zoomed": zoomed,
        }
    except Exception:
        pass


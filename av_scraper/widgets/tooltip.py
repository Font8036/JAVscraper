"""鼠标悬停显示提示的小组件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Literal

_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]

class ToolTip:
    """给任意 widget 挂一个悬停提示。

    用法：
        ToolTip(label_widget, "这是一段说明文字")
    """

    def __init__(
        self,
        widget: tk.Misc,
        text: str,
        *,
        delay: int = 400,
        wraplength: int = 340,
    ):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id = None
        self._tip_window: tk.Toplevel | None = None

        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    # ---------- 对外 ----------
    def update_text(self, text: str) -> None:
        self.text = text

    # ---------- 事件 ----------
    def _on_enter(self, _e=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _on_leave(self, _e=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    # ---------- 显示 / 隐藏 ----------
    def _show(self):
        if self._tip_window is not None:
            return
        if not self.widget.winfo_exists():
            return

        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4

        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)         # 无标题栏 / 无边框
        tw.attributes("-topmost", True)
        tw.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", foreground="#222",
            relief="solid", borderwidth=1,
            padx=6, pady=4,
            wraplength=self.wraplength,
        )
        label.pack()
        self._tip_window = tw

    def _hide(self):
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None

class QuestionMark(ttk.Label):
    """一个蓝色的 '?' 小标签，鼠标悬停显示提示。"""

    def __init__(self, parent, text: str):
        super().__init__(
            parent, 
            text="?", 
            foreground="#0a58ca",
            cursor="question_arrow",
        )
        ToolTip(self, text)


class LabelWithTip(ttk.Frame):
    """把'标签文本'和可选的 '?' 组合成一体。

    用法：
        LabelWithTip(parent, "扫描目录:", tip="要扫描的视频目录")
        LabelWithTip(parent, "输出目录:", tip=None)          # 没有 ?
        LabelWithTip(parent, "输出:", width=10, anchor="e")  # 固定宽度
    """

    def __init__(
        self,
        parent,
        text: str,
        *,
        tip: str | None = None,
        width: int = 0,
        anchor: _AnchorT = "e",
    ):
        super().__init__(parent)
        ttk.Label(self, text=text, width=width, anchor=anchor).pack(side="left")
        if tip:
            QuestionMark(self, tip).pack(side="left", padx=(2, 0))
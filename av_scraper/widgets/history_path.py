"""带历史下拉的路径输入组件。

设计上不感知任何具体配置结构，全部通过回调读写：
- get_value / set_value：当前值的读写（可选持久化）
- get_history / set_history：历史列表的读写
- get_limit：历史条数上限
- save：落盘回调

用法：
    inp = HistoryPathInput(
        parent, "扫描目录:", kind="dir",
        get_value=..., set_value=...,
        get_history=..., set_history=...,
        get_limit=..., save=...,
    )
    inp.pack(fill="x")
    inp.get()      # 取值
    inp.commit()   # 记入历史
    inp.refresh()  # 从配置重读
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Optional


class HistoryPathInput(ttk.Frame):
    def __init__(
        self,
        parent,
        label: str,
        *,
        kind: str = "dir",                # "dir" / "file" / "save"
        filetypes: Optional[list] = None,  # kind="file"/"save" 时使用
        get_value: Callable[[], str] = lambda: "",
        set_value: Callable[[str], None] = lambda v: None,
        get_history: Callable[[], list[str]] = lambda: [],
        set_history: Callable[[list[str]], None] = lambda h: None,
        get_limit: Callable[[], int] = lambda: 5,
        save: Callable[[], None] = lambda: None,
        commit_on_browse: bool = True,
        on_change: Optional[Callable[[], None]] = None,
        label_width: int = 0,
    ):
        super().__init__(parent)
        self._kind = kind
        self._filetypes = filetypes
        self._get_value = get_value
        self._set_value = set_value
        self._get_history = get_history
        self._set_history = set_history
        self._get_limit = get_limit
        self._save = save
        self._commit_on_browse = commit_on_browse
        self._on_change = on_change

        self._suppress_trace = False

        if label_width and label_width > 0:
            ttk.Label(
                self, text=label, width=label_width, anchor="e",
            ).pack(side="left")
        else:
            ttk.Label(self, text=label, anchor="w").pack(side="left")

        self._var = tk.StringVar(value=get_value() or "")
        self._var.trace_add("write", self._on_var_change)

        self._combo = ttk.Combobox(self, textvariable=self._var)
        self._combo.pack(side="left", fill="x", expand=True, padx=4)
        self._combo.configure(values=list(get_history()))
        # 点击输入框任意位置都展开下拉
        self._combo.bind(
            "<Button-1>",
            lambda e: self._combo.after_idle(
                lambda: self._combo.event_generate("<Down>")),
        )

        ttk.Button(self, text="浏览…", command=self._on_browse).pack(side="left")

    # ---------- 对外接口 ----------
    def get(self) -> str:
        return self._var.get().strip()

    def set(self, value: str) -> None:
        self._var.set(value or "")

    def commit(self) -> None:
        """把当前值记入历史（去重、截断到上限、落盘）。"""
        value = self.get()
        if not value:
            return
        history = list(self._get_history())
        deduped = [value] + [d for d in history if d != value]
        limit = max(1, int(self._get_limit()))
        self._set_history(deduped[:limit])
        self._combo.configure(values=list(self._get_history()))
        try:
            self._save()
        except Exception:
            pass

    def refresh(self) -> None:
        """从外部（配置）重新读历史；若当前输入为空则填入历史第一条。"""
        history = list(self._get_history())
        self._combo.configure(values=history)
        if not self.get() and history:
            self._set_silent(history[0])

    # ---------- 内部 ----------
    def _set_silent(self, value: str) -> None:
        self._suppress_trace = True
        try:
            self._var.set(value or "")
        finally:
            self._suppress_trace = False

    def _on_var_change(self, *_args) -> None:
        if self._suppress_trace:
            return
        self._set_value(self._var.get())
        if self._on_change:
            self._on_change()

    def _on_browse(self) -> None:
        cur = self._var.get().strip()
        if self._kind == "dir":
            picked = filedialog.askdirectory(initialdir=cur or None)
        elif self._kind == "file":
            init = str(Path(cur).parent) if cur else None
            picked = filedialog.askopenfilename(
                initialdir=init,
                filetypes=self._filetypes or [("所有文件", "*.*")],
            )
        elif self._kind == "save":
            init = str(Path(cur).parent) if cur else None
            picked = filedialog.asksaveasfilename(initialdir=init)
        else:
            return

        if not picked:
            return
        self._var.set(picked)
        if self._commit_on_browse:
            self.commit()
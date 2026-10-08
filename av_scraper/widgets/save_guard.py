"""保存失败（文件被占用）时的处置：三按钮弹窗 + 可测试的处置状态机。

为什么单独一层：爬虫插件的报告是在 worker 线程里生成的，而 Tk 弹窗只能主线程创建，
所以"生成到临时文件"（后台，可能很慢）和"发布到目标路径"（主线程，毫秒级）是分开的，
见 ``av_scraper/fileio.py``。这里只负责在主线程问用户，并按用户的选择重试**同一份
已经生成好的临时文件** —— 不需要重新爬一遍。

``resolve_locked_target()`` 的 ask / choose_path / timestamp 都能注入，所以
"重试 / 换文件名 / 另存为 / 关闭窗口"这些分支不开窗口也能单测。
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from ..fileio import SaveFailure, classify_save_error, publish, timestamped_path

logger = logging.getLogger(__name__)

_HINT = {
    SaveFailure.LOCKED: "目标文件正被其它程序占用 —— 最常见的原因是它还在 "
                        "Excel / WPS 里开着。",
    SaveFailure.NO_SPACE: "磁盘空间不足，无法写入。",
    SaveFailure.OTHER: "写入目标文件时出错。",
}


def _toplevel(widget):
    """取用于定位 / 作为文件对话框父窗口的顶层窗口。"""
    return widget.winfo_toplevel() if hasattr(widget, "winfo_toplevel") else widget


def ask_locked_action(parent, target, exc) -> str | None:
    """三按钮弹窗：重试 / 换文件名保存 / 另存为…

    返回 "retry" / "rename" / "save_as"；用户直接关掉窗口返回 None
    （没有"取消"按钮，关窗口就是退出，调用方会把临时文件留下）。
    """
    failure = classify_save_error(exc)
    top = _toplevel(parent)
    answer: dict[str, str | None] = {"value": None}

    win = tk.Toplevel(top)
    win.title("无法保存文件")
    win.transient(top)
    win.resizable(False, False)

    body = ttk.Frame(win, padding=12)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="无法保存文件",
              font=("", 10, "bold")).pack(anchor="w")
    ttk.Label(
        body, text=_HINT.get(failure, _HINT[SaveFailure.OTHER]),
        wraplength=460, justify="left",
    ).pack(anchor="w", pady=(6, 4))
    ttk.Label(
        body, text=str(target), wraplength=460, justify="left",
        foreground="#555555",
    ).pack(anchor="w", pady=(0, 10))

    def choose(value: str | None) -> None:
        answer["value"] = value
        win.destroy()

    row = ttk.Frame(body)
    row.pack(fill="x")
    retry_btn = ttk.Button(row, text="重试", command=lambda: choose("retry"))
    retry_btn.pack(side="left")
    ttk.Button(
        row, text="换文件名保存", command=lambda: choose("rename"),
    ).pack(side="left", padx=6)
    ttk.Button(
        row, text="另存为…", command=lambda: choose("save_as"),
    ).pack(side="left")

    win.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    win.bind("<Escape>", lambda _e: choose(None))

    win.update_idletasks()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_width()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    retry_btn.focus_set()
    win.grab_set()
    top.wait_window(win)
    return answer["value"]


def choose_save_as_path(parent, target) -> str | None:
    """让用户挑一个另存位置；取消返回 None。"""
    target = Path(target)
    chosen = filedialog.asksaveasfilename(
        parent=_toplevel(parent),
        title="另存为",
        initialdir=str(target.parent),
        initialfile=target.name,
        defaultextension=target.suffix,
    )
    return chosen or None


def resolve_locked_target(
    parent,
    temp,
    target,
    *,
    ask: Callable[..., str | None] = ask_locked_action,
    choose_path: Callable[..., str | None] = choose_save_as_path,
    timestamp: Callable[..., Path] = timestamped_path,
) -> Path | None:
    """把已生成好的临时文件发布到目标位置；失败就按用户的选择处置。

    返回实际写入的路径；用户关掉弹窗时返回 None（临时文件保留，由调用方记进日志）。
    """
    temp, target = Path(temp), Path(target)
    current = target
    while True:
        try:
            return publish(temp, current)
        except OSError as exc:
            logger.info("保存 %s 失败：%s", current, exc)
            choice = ask(parent, current, exc)
            if choice == "retry":
                continue
            if choice == "rename":
                current = timestamp(target)
                continue
            if choice == "save_as":
                chosen = choose_path(parent, current)
                if chosen:
                    current = Path(chosen)
                continue
            return None

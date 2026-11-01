"""关于对话框 + 检查更新。"""

from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from .. import __version__
from .common import center_and_show, set_window_icon

_GITHUB_REPO = "Font8036/JAVscraper"
_GITHUB_URL = f"https://github.com/{_GITHUB_REPO}"
_RELEASE_API = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"


def _parse_version(s: str) -> tuple[int, ...]:
    """把 "1.2.3" 转成 (1, 2, 3)。宽容处理 v 前缀和 -beta 后缀。"""
    s = s.strip().lstrip("vV")
    # 去掉 -beta / +build 之类
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts = s.split(".")
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def fetch_latest_release() -> dict | None:
    """请求 GitHub API。成功返回 dict，失败返回 None。"""
    try:
        req = urllib.request.Request(
            _RELEASE_API,
            headers={
                "User-Agent": f"JAVscraper/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def _load_about_icon(size: int = 96):
    """加载关于窗口顶部的大图（纯 Tkinter 实现，无第三方依赖）。"""
    try:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            icon_png = base / "assets" / "icon.png"
        else:
            icon_png = (Path(__file__).resolve().parent.parent.parent
                        / "assets" / "icon.png")

        if not icon_png.exists():
            return None

        from tkinter import PhotoImage
        img = PhotoImage(file=str(icon_png))
        
        # 如果原图比目标尺寸大，用整数倍缩小
        if img.width() > size:
            # 计算缩小比例，例如原图 256，目标 96，factor = 2
            factor = max(1, img.width() // size)
            img = img.subsample(factor, factor)
            
        return img
    except Exception:
        return None

def show_about(parent, plugins=None):
    win = tk.Toplevel(parent)
    # 关键：先隐藏，布局完再显示
    win.withdraw()

    win.title("关于 JAVscraper")
    win.resizable(False, False)
    set_window_icon(win)

    frame = ttk.Frame(win, padding=20)
    frame.pack(fill="both", expand=True)

    # ---------- 顶部大图标（居中）----------
    icon_img = _load_about_icon(256)
    if icon_img is not None:
        icon_label = ttk.Label(frame, image=icon_img)
        icon_label.image = icon_img  # type: ignore # 防止被 GC 回收
        icon_label.pack(pady=(0, 12))

    # ---------- 标题 ----------
    ttk.Label(frame, text="JAVscraper",
              font=("", 16, "bold")).pack()
    ttk.Label(frame, text=f"版本 {__version__}",
              foreground="#666").pack(pady=(2, 12))

    # ---------- 简介 ----------
    ttk.Label(
        frame,
        text="文件名刮削与整理工具\n从文件名提取标准代码，批量移动 / 重命名文件。",
        justify="center",
    ).pack(pady=(0, 12))

    # ---------- GitHub 链接 ----------
    link = tk.Label(frame, text="GitHub 仓库",
                    foreground="#0a58ca", cursor="hand2")
    link.pack()
    link.bind("<Button-1>", lambda _e: webbrowser.open(_GITHUB_URL))

    # ---------- 已加载插件 ----------
    if plugins:
        ttk.Separator(frame, orient="horizontal").pack(
            fill="x", pady=12)
        ttk.Label(frame, text="已加载的插件：",
                  foreground="#666").pack(anchor="w")
        for name, version in plugins:
            ttk.Label(
                frame, text=f"  • {name} v{version}",
                foreground="#666",
            ).pack(anchor="w")

    # ---------- 环境 ----------
    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=12)
    env = (
        f"Python {sys.version.split()[0]}\n"
        f"Tkinter {tk.TkVersion}"
    )
    ttk.Label(frame, text=env, foreground="#666",
              justify="left").pack(anchor="w")

    # ---------- 按钮 ----------
    btns = ttk.Frame(frame)
    btns.pack(fill="x", pady=(16, 0))

    check_btn = ttk.Button(btns, text="检查更新")
    check_btn.pack(side="left")
    ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right")

    status_var = tk.StringVar(value="")
    status_label = ttk.Label(frame, textvariable=status_var,
                             foreground="#666", wraplength=380)
    status_label.pack(anchor="w", pady=(8, 0))

    # ---------- 检查更新的逻辑 ----------
    def on_check() -> None:
        check_btn.configure(state="disabled")
        status_var.set("正在检查更新…")

        def worker() -> None:
            release = fetch_latest_release()
            win.after(0, lambda: done(release))

        def done(release) -> None:
            check_btn.configure(state="normal")
            if release is None:
                status_var.set("检查更新失败（网络不可用或被限流）。")
                return
            remote = str(release.get("tag_name", "")).lstrip("vV")
            if not remote:
                status_var.set("未获取到版本信息。")
                return
            if is_newer(remote, __version__):
                status_var.set(f"发现新版本：{remote}")
                notes = (release.get("body") or "").strip()
                html_url = release.get("html_url", _GITHUB_URL + "/releases")
                if messagebox.askyesno(
                    "发现新版本",
                    f"新版本 {remote} 已发布。\n\n"
                    f"{notes[:400]}\n\n"
                    "是否打开下载页面？",
                    parent=win,
                ):
                    webbrowser.open(html_url)
            else:
                status_var.set(f"已是最新版本（{__version__}）。")

        threading.Thread(target=worker, daemon=True).start()

    check_btn.configure(command=on_check)

    # ---------- 显示 + 居中 ----------
    center_and_show(win, parent)
    return win

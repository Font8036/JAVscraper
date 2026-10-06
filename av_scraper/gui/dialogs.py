"""关于对话框 + 检查更新。"""

from __future__ import annotations
import sys
import json
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from .common import set_window_icon
from .. import __version__

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


def _is_newer(remote: str, local: str) -> bool:
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
    """加载关于窗口顶部的大图。

    优先用 Pillow 缩放 icon.png；没装 Pillow 时退回显示原始大小。
    返回 PhotoImage，或 None。
    """
    try:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            icon_png = base / "assets" / "icon.png"
        else:
            icon_png = (Path(__file__).resolve().parent.parent.parent
                        / "assets" / "icon.png")

        if not icon_png.exists():
            return None

        try:
            from PIL import Image, ImageTk
        except ImportError:
            # 没装 Pillow，退回用 tkinter 原生 PhotoImage
            # 原生只支持 PNG/GIF，且不能用 subsample 做非整数缩放
            from tkinter import PhotoImage
            return PhotoImage(file=str(icon_png))

        img = Image.open(icon_png).convert("RGBA")
        # 兼容不同版本的 Pillow
        resample = getattr(getattr(Image, "Resampling", Image),
                           "LANCZOS", None)
        if resample is not None:
            img = img.resize((size, size), resample)
        else:
            img = img.resize((size, size))
        return ImageTk.PhotoImage(img)
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
    icon_img = _load_about_icon(96)
    if icon_img is not None:
        icon_label = ttk.Label(frame, image=icon_img)
        setattr(icon_label, "image", icon_img)   # 保持引用，防止被 GC 回收
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
            if _is_newer(remote, __version__):
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

    # ---------- 居中 + 显示 ----------
    win.update_idletasks()
    width = win.winfo_width()
    height = win.winfo_height()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")

    win.deiconify()      # 布局完成，显示窗口
    return win
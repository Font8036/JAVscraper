"""关于对话框 + 检查更新。"""

from __future__ import annotations

import json
import threading
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

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


def show_about(parent: tk.Misc, plugins: list[tuple[str, str]] | None = None) -> None:
    """关于对话框。"""
    win = tk.Toplevel(parent)
    win.title("关于 JAVscraper")
    win.resizable(False, False)
    win.transient(parent)       # type: ignore[arg-type]

    frame = ttk.Frame(win, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="JAVscraper",
              font=("", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text=f"版本 {__version__}",
              foreground="#666").pack(anchor="w", pady=(0, 12))

    ttk.Label(
        frame,
        text="文件名刮削与整理工具\n从文件名提取标准代码，批量移动 / 重命名文件。",
        justify="left",
    ).pack(anchor="w", pady=(0, 12))

    # GitHub 链接
    link = tk.Label(frame, text="GitHub 仓库",
                    foreground="#0a58ca", cursor="hand2")
    link.pack(anchor="w")
    link.bind("<Button-1>", lambda _e: webbrowser.open(_GITHUB_URL))

    # 已加载插件
    if plugins:
        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(frame, text="已加载的插件：",
                  foreground="#666").pack(anchor="w")
        for name, version in plugins:
            ttk.Label(
                frame, text=f"  • {name} v{version}",
                foreground="#666",
            ).pack(anchor="w")

    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=12)

    env = (
        f"Python {__import__('sys').version.split()[0]}\n"
        f"Tkinter {tk.TkVersion}"
    )
    ttk.Label(frame, text=env, foreground="#666",
              justify="left").pack(anchor="w")

    # 按钮栏
    btns = ttk.Frame(frame)
    btns.pack(fill="x", pady=(16, 0))

    check_btn = ttk.Button(btns, text="检查更新")
    check_btn.pack(side="left")
    ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right")

    status_var = tk.StringVar(value="")
    status_label = ttk.Label(frame, textvariable=status_var,
                             foreground="#666", wraplength=380)
    status_label.pack(anchor="w", pady=(8, 0))

    # ---- 检查更新的逻辑 ----
    def on_check() -> None:
        check_btn.configure(state="disabled")
        status_var.set("正在检查更新…")

        def worker() -> None:
            release = fetch_latest_release()
            # 回到主线程更新 UI
            win.after(0, lambda: done(release))

        def done(release: dict | None) -> None:
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

    # 居中
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
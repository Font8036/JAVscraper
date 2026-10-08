"""配置选项卡：用 ConfigForm 组件渲染三组核心配置。"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import messagebox, ttk

from ..config import AppConfig, ProcessorConfig, ScraperConfig
from ..widgets import ConfigForm


class ConfigTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._forms: dict[str, ConfigForm] = {}
        self._build()
        self.load_from_config()

    # ---------- 构建 ----------
    def _build(self) -> None:
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        vbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_scrollregion():
            if not inner.winfo_exists():
                return
            content_h = inner.winfo_height()
            canvas_h = canvas.winfo_height()
            canvas.configure(scrollregion=(
                0, 0, inner.winfo_width(), max(content_h, canvas_h),
            ))

        def _on_inner_config(_e):
            _sync_scrollregion()

        def _on_canvas_config(e):
            canvas.itemconfig(inner_id, width=e.width)
            _sync_scrollregion()

        inner.bind("<Configure>", _on_inner_config)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # 三个配置分区
        sections = [
            ("scraper", "刮削器", ScraperConfig),
            ("processor", "文件处理器", ProcessorConfig),
            ("app", "应用", AppConfig),
        ]
        for key, title, cls in sections:
            group = ttk.LabelFrame(inner, text=title, padding=8)
            group.pack(fill="x", pady=6)
            form = ConfigForm(group, cls)
            form.pack(fill="x")
            self._forms[key] = form

        # 底部按钮
        btns = ttk.Frame(inner, padding=(8, 4))
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="重新加载",
                   command=self.load_from_config).pack(side="left")
        ttk.Button(btns, text="保存配置",
                   command=self._on_save).pack(side="left", padx=6)
        ttk.Label(
            btns, foreground="#888",
            text=f"配置文件：{self.app.config_path}",
        ).pack(side="left", padx=12)

        self.after(50, _sync_scrollregion)

    # ---------- 读写 ----------
    def load_from_config(self) -> None:
        cfg = self.app.app_config
        self._forms["scraper"].load_from(cfg.scraper)
        self._forms["processor"].load_from(cfg.processor)
        self._forms["app"].load_from(cfg)

    def _on_save(self) -> None:
        try:
            new_scraper = self._forms["scraper"].collect()
            new_processor = self._forms["processor"].collect()
            new_app = self._forms["app"].collect()
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return

        cfg = self.app.app_config
        cfg.scraper = new_scraper
        cfg.processor = new_processor

        # AppConfig 顶层的非嵌套字段
        for f in dataclasses.fields(AppConfig):
            if f.name in ("scraper", "processor"):
                continue
            setattr(cfg, f.name, getattr(new_app, f.name))

        try:
            cfg.save(self.app.config_path)
        except OSError as e:
            messagebox.showerror("保存失败", str(e))
            return

        self.app.refresh_from_config()
        self.load_from_config()
        messagebox.showinfo("成功", "配置已保存。")

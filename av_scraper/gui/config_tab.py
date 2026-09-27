"""配置选项卡：根据 dataclass metadata 自动生成控件。"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from dataclasses import fields
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ..config import AppConfig, ProcessorConfig, ScraperConfig


class ConfigTab(ttk.Frame):
    # (section, field_name) -> (kind, widget_or_var)
    _widgets: dict[tuple[str, str], tuple[str, Any]]

    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._widgets = {}
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
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        self._render_section(inner, "scraper", "刮削器", ScraperConfig)
        self._render_section(inner, "processor", "文件处理器", ProcessorConfig)
        self._render_section(inner, "app", "应用", AppConfig)

        btns = ttk.Frame(inner)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="重新加载", command=self.load_from_config).pack(side="left")
        ttk.Button(btns, text="保存配置", command=self._on_save).pack(side="left", padx=6)
        ttk.Label(
            btns, foreground="#888",
            text=f"配置文件：{self.app.config_path}",
        ).pack(side="left", padx=12)

    def _render_section(self, parent, section: str, title: str, cls) -> None:
        group = ttk.LabelFrame(parent, text=title, padding=8)
        group.pack(fill="x", pady=6)

        for f in fields(cls):
            if f.metadata.get("hidden"):
                continue
            # 新增：跳过嵌套的 dataclass 字段
            if f.name in ("scraper", "processor"):
                continue
            row = ttk.Frame(group)
            row.pack(fill="x", pady=2)
            label = f.metadata.get("label", f.name)
            kind = f.metadata.get("kind", "str")
            ttk.Label(row, text=label + ":", width=22, anchor="e").pack(side="left")

            widget = self._make_widget(row, kind, f)
            self._widgets[(section, f.name)] = (kind, widget)

    def _make_widget(self, row, kind: str, f: dataclasses.Field) -> Any:
        if kind == "bool":
            var = tk.BooleanVar()
            ttk.Checkbutton(row, variable=var).pack(side="left", padx=4)
            return var

        if kind == "choice":
            var = tk.StringVar()
            ttk.Combobox(
                row, textvariable=var,
                values=list(f.metadata.get("choices", [])),
                state="readonly", width=16,
            ).pack(side="left", padx=4)
            return var
        
        if kind == "int":
            var = tk.StringVar()
            ttk.Spinbox(row, from_=1, to=100, textvariable=var, width=8).pack(
                side="left", padx=4)
            return var

        if kind == "list":
            height = 14 if f.metadata.get("big") else 4
            txt = tk.Text(row, height=height, wrap="none")
            txt.pack(side="left", fill="both", expand=True, padx=4)
            return txt

        # str / dir / file
        var = tk.StringVar()
        ttk.Entry(row, textvariable=var).pack(
            side="left", fill="x", expand=True, padx=4)
        if kind in ("dir", "file"):
            ttk.Button(
                row, text="浏览…",
                command=lambda v=var, k=kind: self._browse(v, k),
            ).pack(side="left")
        return var

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "dir":
            d = filedialog.askdirectory(initialdir=var.get() or None)
            if d:
                var.set(d)
        else:
            f = filedialog.askopenfilename(
                initialdir=var.get() or None,
                filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
            )
            if f:
                var.set(f)

    # ---------- 读写 ----------
    def load_from_config(self) -> None:
        for section, cfg in (("scraper", self.app.app_config.scraper),
                             ("processor", self.app.app_config.processor),
                             ("app", self.app.app_config)):
            for f in fields(type(cfg)):
                key = (section, f.name)
                if key not in self._widgets:
                    continue
                kind, widget = self._widgets[key]
                value = getattr(cfg, f.name)
                self._write_widget(kind, widget, value)

    @staticmethod
    def _write_widget(kind: str, widget: Any, value: Any) -> None:
        if kind == "bool":
            widget.set(bool(value))
        elif kind in ("choice", "str", "dir", "file"):
            widget.set(str(value))
        elif kind == "list":
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(str(v) for v in value))
        elif kind == "int":
            widget.set(str(int(value)))

    def _on_save(self) -> None:
        try:
            scraper = self._collect(ScraperConfig, "scraper")
            processor = self._collect(ProcessorConfig, "processor")
            app_section = self._collect(AppConfig, "app")
        except Exception as e:
            messagebox.showerror("保存失败", f"{e}")
            return

        self.app.app_config.scraper = scraper
        self.app.app_config.processor = processor
        self.app.app_config.confirm_on_close = app_section.confirm_on_close
        try:
            self.app.app_config.save(self.app.config_path)
        except OSError as e:
            messagebox.showerror("保存失败", f"{e}")
            return

        self.app.refresh_from_config()
        messagebox.showinfo("成功", "配置已保存。")

    def _collect(self, cls, section: str):
        # "app" 段的 current 是 AppConfig 本身，其它段是对应子对象
        if section == "app":
            current = self.app.app_config
        else:
            current = getattr(self.app.app_config, section)
            
        kwargs = {}
        for f in fields(cls):
            if f.metadata.get("hidden"):
                # hidden 字段没有控件，保留当前值，避免被默认值覆盖
                kwargs[f.name] = getattr(current, f.name)
                continue
            if f.name in ("scraper", "processor"):
                # 嵌套字段由各自的 section 处理
                kwargs[f.name] = getattr(current, f.name)
                continue
            kind, widget = self._widgets[(section, f.name)]
            if kind == "bool":
                kwargs[f.name] = bool(widget.get())
            elif kind in ("choice", "str", "dir", "file"):
                kwargs[f.name] = widget.get().strip()
            elif kind == "list":
                kwargs[f.name] = self._parse_list(widget.get("1.0", "end"))
            elif kind == "int":
                kwargs[f.name] = int(widget.get())
        return cls(**kwargs)

    @staticmethod
    def _parse_list(text: str) -> list[str]:
        items: list[str] = []
        for line in text.strip().splitlines():
            for part in line.split(","):
                part = part.strip().strip("'\"").strip()
                if part:
                    items.append(part)
        return items
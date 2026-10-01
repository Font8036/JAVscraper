"""配置选项卡：根据 dataclass metadata 自动生成控件。"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from dataclasses import fields
from tkinter import filedialog, messagebox, ttk
from typing import Any
from ..widgets import ToolTip, LabelWithTip, QuestionMark
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
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_scrollregion():
            """scrollregion 至少覆盖可视区域，避免点滚动条箭头出现空档。"""
            if not inner.winfo_exists():
                return
            content_h = inner.winfo_height()
            canvas_h = canvas.winfo_height()
            y1 = max(content_h, canvas_h)
            canvas.configure(
                scrollregion=(0, 0, inner.winfo_width(), y1)
            )

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

        self._render_section(inner, "scraper", "刮削器", ScraperConfig)
        self._render_section(inner, "processor", "文件处理器", ProcessorConfig)
        self._render_section(inner, "app", "应用", AppConfig)

        # 底部按钮（保持原样）
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

    def _render_section(self, parent, section: str, title: str, cls) -> None:
        group = ttk.LabelFrame(parent, text=title, padding=8)
        group.pack(fill="x", pady=6)

        visible = [f for f in fields(cls) if not f.metadata.get("hidden")]
        label_width = self._calc_label_width(visible)

        for row_fields in self._group_row_fields(visible):
            self._render_row(group, section, row_fields, label_width)

    @staticmethod
    def _calc_label_width(visible_fields) -> int:
        """只按左侧有独立标签的字段计算宽度。
        勾选框（bool）的文本在按钮右侧，list 的标签在顶部，都不参与。"""
        max_w = 0
        for f in visible_fields:
            kind = f.metadata.get("kind", "str")
            if kind in ("bool", "list"):
                continue
            label = f.metadata.get("label", f.name) + ":"
            w = sum(2 if ord(c) > 127 else 1 for c in label)
            max_w = max(max_w, w)
        return max(8, max_w)

    @staticmethod
    def _group_row_fields(all_fields) -> list[list]:
        """all_fields 已过滤 hidden，按 row_group 聚为行。"""
        result: list[list] = []
        buf: list = []
        buf_group = None

        for f in all_fields:
            rg = f.metadata.get("row_group")
            if rg is None:
                if buf:
                    result.append(buf)
                    buf = []
                    buf_group = None
                result.append([f])
            else:
                if rg == buf_group:
                    buf.append(f)
                else:
                    if buf:
                        result.append(buf)
                    buf = [f]
                    buf_group = rg

        if buf:
            result.append(buf)
        return result

    def _render_row(self, parent, section: str, row_fields: list,
                    label_width: int) -> None:
        kinds = {f.metadata.get("kind", "str") for f in row_fields}

        # 所有 bool（单个或一组）都走 bool 行，保证"按钮在前，文本在后"
        if kinds == {"bool"}:
            self._render_bool_row(parent, section, row_fields, label_width)
            return

        if len(row_fields) == 1:
            self._render_single(parent, section, row_fields[0], label_width)
            return

        if kinds == {"list"}:
            self._render_list_row(parent, section, row_fields)
            return

        # 其它混合类型：逐字段成行
        for f in row_fields:
            self._render_single(parent, section, f, label_width)

    def _render_single(self, parent, section: str, f,
                       label_width: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)

        label = f.metadata.get("label", f.name)
        kind = f.metadata.get("kind", "str")
        tip = f.metadata.get("tooltip")

        LabelWithTip(
            row, label + ":", tip=tip,
            width=label_width, anchor="e",
        ).pack(side="left")

        widget = self._make_widget(row, kind, f)
        self._widgets[(section, f.name)] = (kind, widget)

    def _render_bool_row(self, parent, section: str, row_fields: list,
                         label_width: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="", width=label_width).pack(side="left")

        for i, f in enumerate(row_fields):
            label = f.metadata.get("label", f.name)
            tip = f.metadata.get("tooltip")
            var = tk.BooleanVar()
            pad = (0, 20) if i < len(row_fields) - 1 else (0, 0)

            cell = ttk.Frame(row)
            cell.pack(side="left", padx=pad)
            ttk.Checkbutton(cell, text=label, variable=var).pack(side="left")
            if tip:
                QuestionMark(cell, tip).pack(side="left", padx=(2, 0))

            self._widgets[(section, f.name)] = ("bool", var)

    def _render_list_row(self, parent, section: str, row_fields: list) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)

        for i in range(len(row_fields)):
            row.columnconfigure(i, weight=1, uniform="list_group")

        for i, f in enumerate(row_fields):
            cell = ttk.Frame(row)
            cell.grid(row=0, column=i, sticky="nsew",
                      padx=(4 if i > 0 else 0, 4))

            label = f.metadata.get("label", f.name)
            tip = f.metadata.get("tooltip")

            LabelWithTip(
                cell, label + ":", tip=tip,
            ).pack(anchor="w", fill="x")

            height = 14 if f.metadata.get("big") else 4
            txt = tk.Text(cell, height=height, wrap="none", width=1)
            txt.pack(fill="both", expand=True)
            self._widgets[(section, f.name)] = ("list", txt)


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
            txt = tk.Text(row, height=height, wrap="none", width=1)
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
        
        for f in dataclasses.fields(AppConfig):
            if f.name in ("scraper", "processor"):
                continue
            setattr(self.app.app_config, f.name, getattr(app_section, f.name))
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
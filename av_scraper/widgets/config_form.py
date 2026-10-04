"""按 dataclass 定义渲染配置控件的可复用组件。

用法：
    form = ConfigForm(parent, ScraperConfig)
    form.pack(fill="both", expand=True)
    form.load_from(current_scraper)      # 用现有实例填充控件
    ...
    new_config = form.collect()          # 从控件读值，返回新实例

metadata 约定（与核心 config 保持一致）：
- label:       显示名
- kind:        str / dir / file / save / bool / choice / int / float / list
- choices:     kind="choice" 时的选项
- hidden:      True 时不渲染控件，collect 时从 base 实例保留原值
- big:         kind="list" 时用高文本框
- row_group:   相邻且同名的字段会排在同一水平行
- tooltip:     悬停提示文字
"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from dataclasses import fields
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Optional

from .tooltip import LabelWithTip, QuestionMark

# row_group 行的左缩进宽度（字符数），用于让 checkbox/mixed 行不跟着长输入框对齐
_ROW_GROUP_INDENT = 2

class ConfigForm(ttk.Frame):
    def __init__(self, parent, config_cls, instance: Any = None):
        super().__init__(parent)
        self._config_cls = config_cls
        self._widgets: dict[str, tuple[str, Any]] = {}
        self._row_frames: dict[str, ttk.Frame] = {}    # ← 新增
        self._loaded_instance: Optional[Any] = None

        self._build()
        if instance is not None:
            self.load_from(instance)

    # ============================================================
    # 构建
    # ============================================================
    def _build(self) -> None:
        visible = [f for f in fields(self._config_cls)
                   if not f.metadata.get("hidden")]
        label_width = self._calc_label_width(visible)

        for row_fields in self._group_row_fields(visible):
            self._render_row(row_fields, label_width)

    @staticmethod
    def _calc_label_width(visible_fields) -> int:
        """只按"独立成行"的字段计算左侧预留宽度。

        跳过：
        - bool：文字在按钮右侧，不占左侧
        - list：标签在顶部，不占左侧
        - row_group 里的字段：和其它字段共用一行，标签不参与左对齐
        """
        max_w = 0
        for f in visible_fields:
            kind = f.metadata.get("kind", "str")
            if kind in ("bool", "list"):
                continue
            if f.metadata.get("row_group"):
                continue
            label = f.metadata.get("label", f.name) + ":"
            w = int(sum((1.8 if ord(c) > 127 else 1) for c in label))
            max_w = max(max_w, w)
        return max(6, max_w)

    @staticmethod
    def _group_row_fields(all_fields) -> list[list]:
        """把连续且 row_group 相同的字段聚为一行。"""
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

    def _render_row(self, row_fields: list, label_width: int) -> None:
        kinds = {f.metadata.get("kind", "str") for f in row_fields}

        if kinds == {"bool"}:
            self._render_bool_row(row_fields, label_width)
            return

        if len(row_fields) == 1:
            self._render_single(row_fields[0], label_width)
            return

        if kinds == {"list"}:
            self._render_list_row(row_fields)
            return

        self._render_mixed_row(row_fields, label_width)

    def _render_single(self, f, label_width: int) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=2)
        self._row_frames[f.name] = row                  # ← 这一行

        kind = f.metadata.get("kind", "str")

        # 长条输入框贴左；短字段缩进，与 bool 行对齐
        if kind not in ("str", "dir", "file", "save"):
            ttk.Label(row, text="", width=_ROW_GROUP_INDENT).pack(side="left")

        label = f.metadata.get("label", f.name)
        tip = f.metadata.get("tooltip")

        LabelWithTip(
            row, label + ":", tip=tip,
            width=label_width, anchor="e",
        ).pack(side="left")

        widget = self._make_widget(row, f)
        self._widgets[f.name] = (kind, widget)

    def _render_bool_row(self, row_fields: list, label_width: int) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="", width=_ROW_GROUP_INDENT).pack(side="left")

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

            self._widgets[f.name] = ("bool", var)
            self._row_frames[f.name] = row

    def _render_list_row(self, row_fields: list) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=2)
        for i in range(len(row_fields)):
            row.columnconfigure(i, weight=1, uniform="list_group")

        for i, f in enumerate(row_fields):
            cell = ttk.Frame(row)
            cell.grid(row=0, column=i, sticky="nsew",
                      padx=(4 if i > 0 else 0, 4))
            label = f.metadata.get("label", f.name)
            tip = f.metadata.get("tooltip")
            LabelWithTip(cell, label + ":", tip=tip).pack(anchor="w", fill="x")

            height = 14 if f.metadata.get("big") else 4
            txt = tk.Text(cell, height=height, wrap="none", width=1)
            txt.pack(fill="both", expand=True)
            self._widgets[f.name] = ("list", txt)
            self._row_frames[f.name] = cell

    def _render_mixed_row(self, row_fields: list, label_width: int) -> None:
        """混合类型行：bool 显示为 Checkbutton，其它显示为 标签+控件。"""
        row = ttk.Frame(self)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="", width=_ROW_GROUP_INDENT).pack(side="left")

        for i, f in enumerate(row_fields):
            kind = f.metadata.get("kind", "str")
            label = f.metadata.get("label", f.name)
            tip = f.metadata.get("tooltip")
            pad_right = 16 if i < len(row_fields) - 1 else 0

            cell = ttk.Frame(row)
            cell.pack(side="left", padx=(0, pad_right))
            self._row_frames[f.name] = cell

            if kind == "bool":
                var = tk.BooleanVar()
                ttk.Checkbutton(cell, text=label, variable=var).pack(side="left")
                if tip:
                    QuestionMark(cell, tip).pack(side="left", padx=(2, 0))
                self._widgets[f.name] = ("bool", var)
            else:
                ttk.Label(cell, text=label + ":").pack(side="left")
                if tip:
                    QuestionMark(cell, tip).pack(side="left", padx=(2, 0))
                widget = self._make_widget(cell, f)
                self._widgets[f.name] = (kind, widget)

    # ============================================================
    # 控件构造
    # ============================================================
    def _make_widget(self, parent, f) -> Any:
        kind = f.metadata.get("kind", "str")

        if kind == "bool":
            var = tk.BooleanVar()
            ttk.Checkbutton(parent, variable=var).pack(side="left", padx=4)
            return var

        if kind == "choice":
            var = tk.StringVar()
            ttk.Combobox(
                parent, textvariable=var,
                values=list(f.metadata.get("choices", [])),
                state="readonly", width=16,
            ).pack(side="left", padx=4)
            return var

        if kind == "int":
            var = tk.StringVar()
            ttk.Spinbox(
                parent, from_=0, to=100_000, textvariable=var, width=8,
            ).pack(side="left", padx=4)
            return var

        if kind == "float":
            var = tk.StringVar()
            ttk.Spinbox(
                parent, from_=0.0, to=1_000.0, increment=0.1,
                textvariable=var, width=8,
            ).pack(side="left", padx=4)
            return var

        if kind == "list":
            height = 14 if f.metadata.get("big") else 4
            txt = tk.Text(parent, height=height, wrap="none", width=1)
            txt.pack(side="left", fill="both", expand=True, padx=4)
            return txt

        # str / dir / file / save
        var = tk.StringVar()
        entry_width = f.metadata.get("width", 20)
        ttk.Entry(parent, textvariable=var, width=entry_width).pack(
            side="left", fill="x", expand=True, padx=4)
        if kind in ("dir", "file", "save"):
            ext = f.metadata.get("ext")
            ttk.Button(
                parent, text="浏览…",
                command=lambda v=var, k=kind, e=ext: self._browse(v, k, e),
            ).pack(side="left")
        return var

    def _browse(self, var: tk.StringVar, kind: str,
                ext: Optional[str] = None) -> None:
        cur = var.get().strip()

        if kind == "dir":
            picked = filedialog.askdirectory(initialdir=cur or None)

        elif kind == "file":
            init = str(Path(cur).parent) if cur else None
            kwargs: dict[str, Any] = {"initialdir": init}
            if ext:
                kwargs["filetypes"] = [
                    (f"{ext.lstrip('.').upper()} 文件", f"*{ext}"),
                    ("所有文件", "*.*"),
                ]
            picked = filedialog.askopenfilename(**kwargs)

        elif kind == "save":
            init = str(Path(cur).parent) if cur else None
            kwargs = {"initialdir": init}
            if ext:
                kwargs["defaultextension"] = ext
                kwargs["filetypes"] = [
                    (f"{ext.lstrip('.').upper()} 文件", f"*{ext}"),
                    ("所有文件", "*.*"),
                ]
            picked = filedialog.asksaveasfilename(**kwargs)

        else:
            return

        if picked:
            var.set(picked)

    # ============================================================
    # 读写接口
    # ============================================================
    def load_from(self, instance: Any) -> None:
        """从实例填充控件。"""
        self._loaded_instance = instance
        for f in dataclasses.fields(self._config_cls):
            if f.name not in self._widgets:
                continue
            kind, widget = self._widgets[f.name]
            value = getattr(instance, f.name, None)
            self._write_widget(kind, widget, value)

    def collect(self, base: Any = None) -> Any:
        """从控件读值，返回新的配置实例。

        base 用于提供隐藏字段的值。默认用最近 load_from 的实例，
        都没有则用默认构造的实例。
        """
        if base is None:
            base = self._loaded_instance
        if base is None:
            base = self._config_cls()

        kwargs = {}
        for f in dataclasses.fields(self._config_cls):
            # 隐藏字段：从 base 取原值
            if f.metadata.get("hidden") or f.name not in self._widgets:
                kwargs[f.name] = getattr(base, f.name, None)
                continue
            kind, widget = self._widgets[f.name]
            kwargs[f.name] = self._read_widget(f, kind, widget)
        return self._config_cls(**kwargs)

    def get_row_frame(self, field_name: str) -> Optional[ttk.Frame]:
        """返回指定字段所在的行 Frame。可往里添加额外的按钮等控件。"""
        row = self._row_frames.get(field_name)
        if row is None:
            import logging
            logging.getLogger(__name__).warning(
                "get_row_frame: 未找到字段 %r 的行（是否漏记 _row_frames？）",
                field_name,
            )
        return row

    # ---------- widget 读写 ----------
    @staticmethod
    def _write_widget(kind: str, widget: Any, value: Any) -> None:
        if kind == "bool":
            widget.set(bool(value))
        elif kind in ("choice", "str", "dir", "file", "save", "int", "float"):
            widget.set("" if value is None else str(value))
        elif kind == "list":
            widget.delete("1.0", "end")
            if value:
                widget.insert("1.0", "\n".join(str(v) for v in value))

    @staticmethod
    def _read_widget(f, kind: str, widget: Any) -> Any:
        if kind == "bool":
            return bool(widget.get())
        if kind == "choice":
            return widget.get()
        if kind in ("str", "dir", "file", "save"):
            return widget.get().strip()
        if kind == "int":
            try:
                return int(widget.get())
            except (TypeError, ValueError):
                d = f.default
                return d if d is not dataclasses.MISSING else 0
        if kind == "float":
            try:
                return float(widget.get())
            except (TypeError, ValueError):
                d = f.default
                return d if d is not dataclasses.MISSING else 0.0
        if kind == "list":
            text = widget.get("1.0", "end")
            items: list[str] = []
            for line in text.strip().splitlines():
                for part in line.split(","):
                    part = part.strip().strip("'\"").strip()
                    if part:
                        items.append(part)
            return items
        return widget.get()
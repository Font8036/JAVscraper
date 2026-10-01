"""带排序和搜索功能的 Treeview 组件。

设计目标：
- 数据是"黑盒对象"，组件不感知其结构
- 通过 Column.display / Column.sort 回调把数据翻译成显示值和排序键
- 点击表头在 升序 → 降序 → 原始顺序 三态之间循环
- 搜索框实时过滤：子串匹配全部列 / 单列 / 正则
- append / set_data / update_row 时保持当前排序与过滤

未来扩展预留：
- 勾选框、导出 CSV、列隐藏等可在现有接口之上平滑加入
"""

from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Literal, Optional

_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]

# 搜索框下拉里用到的两个特殊模式
_MODE_ALL = "全部列"
_MODE_REGEX = "正则"


@dataclass
class Column:
    key: str
    header: str
    width: int
    display: Callable[[Any], str]
    sort: Optional[Callable[[Any], Any]] = None
    anchor: _AnchorT = "w"
    stretch: bool = False
    sortable: bool = True
    searchable: bool = True


class SortableTreeview(ttk.Frame):
    def __init__(
        self,
        parent,
        columns: list[Column],
        *,
        key: Optional[Callable[[Any], str]] = None,
        row_tags: Optional[Callable[[Any], tuple[str, ...]]] = None,
        tag_configure: Optional[dict[str, dict]] = None,
        height: int = 16,
        searchable: bool = False,
        filter_func: Optional[Callable[[Any, str], bool]] = None,
        on_sort_changed: Optional[Callable[[Optional[str], str], None]] = None,
    ):
        super().__init__(parent)
        self._columns = list(columns)
        self._columns_by_key = {c.key: c for c in columns}
        self._row_tags = row_tags or (lambda _d: ())
        self._on_sort_changed = on_sort_changed
        self._key = key or (lambda d: str(id(d)))
        self._custom_filter_func = filter_func
        self._height = height

        # 数据
        self._original_data: list[Any] = []
        self._visible_data: list[Any] = []
        self._data_by_iid: dict[str, Any] = {}

        # 排序状态
        self._sort_column: Optional[str] = None
        self._sort_direction: str = "original"   # asc / desc / original

        # 搜索状态
        self._filter_text = ""
        self._search_var: Optional[tk.StringVar] = None
        self._mode_var: Optional[tk.StringVar] = None
        self._match_label_var: Optional[tk.StringVar] = None

        # 搜索栏（可选）
        if searchable:
            self._build_search_bar()

        # 表格
        self._build_tree()

        for name, opts in (tag_configure or {}).items():
            self.tree.tag_configure(name, **opts)

    # ============================================================
    # 构建
    # ============================================================
    def _build_search_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))

        ttk.Label(bar, text="搜索:").pack(side="left")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        entry = ttk.Entry(bar, textvariable=self._search_var)
        entry.pack(side="left", fill="x", expand=True, padx=(4, 4))

        self._mode_var = tk.StringVar(value=_MODE_ALL)
        combo = ttk.Combobox(
            bar, textvariable=self._mode_var,
            values=self._mode_values(),
            state="readonly", width=12,
        )
        combo.pack(side="left", padx=(0, 6))
        combo.bind("<<ComboboxSelected>>", self._on_mode_changed)

        self._match_label_var = tk.StringVar(value="")
        ttk.Label(
            bar, textvariable=self._match_label_var,
            foreground="#666", width=10, anchor="e",
        ).pack(side="left")

    def _mode_values(self) -> list[str]:
        """下拉框选项：全部列 / 各可见列 / 正则。"""
        values = [_MODE_ALL]
        for c in self._columns:
            if c.header and c.searchable:
                values.append(c.header)
        values.append(_MODE_REGEX)
        return values

    def _build_tree(self) -> None:
        self.tree = ttk.Treeview(
            self, columns=[c.key for c in self._columns],
            show="headings", height=self._height,
        )
        for c in self._columns:
            if c.sortable:
                self.tree.heading(
                    c.key, text=c.header,
                    command=lambda key=c.key: self._on_header_click(key),
                )
            else:
                self.tree.heading(c.key, text=c.header)
            self.tree.column(
                c.key, width=c.width, anchor=c.anchor,
                stretch=c.stretch, minwidth=40,
            )

        vbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

    # ============================================================
    # 数据操作
    # ============================================================
    def clear(self) -> None:
        self._original_data.clear()
        self._visible_data.clear()
        self._data_by_iid.clear()
        self._sort_column = None
        self._sort_direction = "original"
        self._filter_text = ""
        if self._search_var is not None:
            # 避免触发 trace 造成递归
            self._search_var.set("")
        if self._mode_var is not None:
            self._mode_var.set(_MODE_ALL)
        self.tree.delete(*self.tree.get_children())
        self._update_headers()
        self._update_match_count()

    def set_data(self, rows: list[Any]) -> None:
        self._original_data = list(rows)
        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()

    def append_row(self, data: Any, *, scroll: bool = True) -> None:
        self._original_data.append(data)

        # 过滤或排序状态下，重新计算
        if self._filter_text or self._sort_direction != "original":
            if self._filter_text and not self._match(data, self._filter_text):
                # 不匹配当前过滤条件，不显示
                self._update_match_count()
                return
            self._apply_filter_and_sort()
            self._redraw()
            self._update_match_count()
            if scroll:
                iid = self._key(data)
                if self.tree.exists(iid):
                    self.tree.see(iid)
            return

        # 原始顺序：直接追加
        self._visible_data.append(data)
        self._insert_one(data)
        if scroll:
            kids = self.tree.get_children()
            if kids:
                self.tree.see(kids[-1])
        self._update_match_count()

    def update_row(self, data: Any) -> None:
        iid = self._key(data)
        self._data_by_iid[iid] = data

        for i, d in enumerate(self._original_data):
            if self._key(d) == iid:
                self._original_data[i] = data
                break
        for i, d in enumerate(self._visible_data):
            if self._key(d) == iid:
                self._visible_data[i] = data
                break

        if self._filter_text:
            self._apply_filter_and_sort()
            self._redraw()
            self._update_match_count()
            return

        if self.tree.exists(iid):
            values = tuple(self._render_cell(c, data) for c in self._columns)
            tags = self._row_tags(data)
            self.tree.item(iid, values=values, tags=tags)

    def see_row(self, data: Any) -> None:
        iid = self._key(data)
        if self.tree.exists(iid):
            self.tree.see(iid)

    # ============================================================
    # 排序
    # ============================================================
    def get_sort_state(self) -> tuple[Optional[str], str]:
        return self._sort_column, self._sort_direction

    def set_sort_state(self, column: Optional[str], direction: str) -> None:
        if direction not in ("asc", "desc", "original"):
            raise ValueError(f"未知排序方向: {direction}")
        if direction == "original":
            self._sort_column = None
            self._sort_direction = "original"
        else:
            if column not in self._columns_by_key:
                raise ValueError(f"未知列: {column}")
            self._sort_column = column
            self._sort_direction = direction
        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()
        self._update_headers()

    def _on_header_click(self, key: str) -> None:
        if key == self._sort_column:
            if self._sort_direction == "asc":
                self._sort_direction = "desc"
            elif self._sort_direction == "desc":
                self._sort_column = None
                self._sort_direction = "original"
            else:
                self._sort_direction = "asc"
        else:
            self._sort_column = key
            self._sort_direction = "asc"

        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()
        self._update_headers()

        if self._on_sort_changed:
            self._on_sort_changed(self._sort_column, self._sort_direction)

    def _apply_filter_and_sort(self) -> None:
        # 1. 过滤
        base = list(self._original_data)
        if self._filter_text:
            base = [d for d in base if self._match(d, self._filter_text)]

        # 2. 排序
        if self._sort_direction == "original" or self._sort_column is None:
            self._visible_data = base
        else:
            col = self._columns_by_key[self._sort_column]
            sort_fn = col.sort if col.sort is not None else col.display
            reverse = (self._sort_direction == "desc")
            self._visible_data = sorted(base, key=sort_fn, reverse=reverse)

    def _update_headers(self) -> None:
        for c in self._columns:
            if not c.sortable:
                continue
            text = c.header
            if c.key == self._sort_column and self._sort_direction != "original":
                text += " ▼" if self._sort_direction == "desc" else " ▲"
            self.tree.heading(c.key, text=text)

    # ============================================================
    # 搜索
    # ============================================================
    def _on_search_changed(self, *_args) -> None:
        if self._search_var is None:
            return
        self._filter_text = self._search_var.get()
        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()

    def _on_mode_changed(self, _e=None) -> None:
        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()

    def _match(self, data: Any, keyword: str) -> bool:
        # 用户自定义匹配优先
        if self._custom_filter_func is not None:
            return bool(self._custom_filter_func(data, keyword))

        mode = self._mode_var.get() if self._mode_var is not None else _MODE_ALL

        if mode == _MODE_REGEX:
            try:
                pat = re.compile(keyword, re.IGNORECASE)
            except re.error:
                return False
            return any(pat.search(self._render_cell(c, data)) for c in self._columns)

        if mode == _MODE_ALL:
            kw = keyword.lower()
            return any(
                kw in self._render_cell(c, data).lower() for c in self._columns
            )

        # 某一列
        col = next(
            (c for c in self._columns if c.header == mode and c.searchable),
            None,
        )
        if col is None:
            return False
        return keyword.lower() in self._render_cell(col, data).lower()

    def _update_match_count(self) -> None:
        if self._match_label_var is None:
            return
        total = len(self._original_data)
        shown = len(self._visible_data)
        if self._filter_text:
            self._match_label_var.set(f"{shown}/{total}")
        else:
            self._match_label_var.set(f"{total}")

    # ============================================================
    # 渲染
    # ============================================================
    def _redraw(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._data_by_iid.clear()
        for data in self._visible_data:
            self._insert_one(data)

    def _insert_one(self, data: Any) -> None:
        values = tuple(self._render_cell(c, data) for c in self._columns)
        tags = self._row_tags(data)
        iid = self._key(data)
        self.tree.insert("", "end", iid=iid, values=values, tags=tags)
        self._data_by_iid[iid] = data

    @staticmethod
    def _render_cell(col: Column, data: Any) -> str:
        try:
            v = col.display(data)
        except Exception:
            return ""
        return "" if v is None else str(v)

    # ============================================================
    # 只读接口
    # ============================================================
    @property
    def visible_data(self) -> list[Any]:
        return list(self._visible_data)

    @property
    def original_data(self) -> list[Any]:
        return list(self._original_data)
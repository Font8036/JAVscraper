"""带排序、搜索、勾选功能的 Treeview 组件。

设计目标：
- 数据是"黑盒对象"，组件不感知其结构
- 通过 Column.display / Column.sort 回调把数据翻译成显示值和排序键
- 点击表头在 升序 → 降序 → 原始顺序 三态之间循环
- 搜索框实时过滤：子串匹配全部列 / 单列 / 正则
- 勾选框列（可选）：Unicode 符号 + 已勾选行高亮

勾选框列的使用：
    在 columns 里加一项 Column("__check__", "", 40, kind="checkbox")
    组件自动开启勾选功能，其余列照旧。
"""

from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Literal, Optional

_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]
_COL_ALL = "全部列"
_FILTER_ALL = "全部"
_FILTER_CHECKED = "已勾选"
_FILTER_UNCHECKED = "未勾选"
# 勾选框符号
_SYM_CHECKED = "☑"
_SYM_UNCHECKED = "☐"
_SYM_PARTIAL = "▣"


@dataclass
class Column:
    key: str
    header: str
    width: int
    display: Callable[[Any], str] = lambda _d: ""
    sort: Optional[Callable[[Any], Any]] = None
    anchor: _AnchorT = "w"
    stretch: bool = False
    sortable: bool = True
    searchable: bool = True
    kind: str = "text"        # "text" / "checkbox"


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
        on_selection_changed: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self._columns = list(columns)
        self._columns_by_key = {c.key: c for c in columns}
        self._row_tags = row_tags or (lambda _d: ())
        self._on_sort_changed = on_sort_changed
        self._on_selection_changed = on_selection_changed
        self._key = key or (lambda d: str(id(d)))
        self._custom_filter_func = filter_func
        self._height = height

        # 数据
        self._original_data: list[Any] = []
        self._visible_data: list[Any] = []
        self._data_by_iid: dict[str, Any] = {}

        # 排序状态
        self._sort_column: Optional[str] = None
        self._sort_direction: str = "original"

        # 搜索状态
        self._filter_text = ""
        self._search_var: Optional[tk.StringVar] = None
        self._col_var: Optional[tk.StringVar] = None
        self._filter_var: Optional[tk.StringVar] = None
        self._regex_var: Optional[tk.BooleanVar] = None
        self._match_label_var: Optional[tk.StringVar] = None

        # 勾选状态
        self._checkbox_key: Optional[str] = next(
            (c.key for c in columns if c.kind == "checkbox"), None,
        )
        self._selected: set[str] = set()

        if searchable:
            self._build_search_bar()

        self._build_tree()

        for name, opts in (tag_configure or {}).items():
            self.tree.tag_configure(name, **opts)
        # 勾选行高亮
        if self._checkbox_key is not None:
            self.tree.tag_configure("__checked__", background="#eef4ff")

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

        # 列选择下拉
        self._col_var = tk.StringVar(value=_COL_ALL)
        col_combo = ttk.Combobox(
            bar, textvariable=self._col_var,
            values=self._column_values(),
            state="readonly", width=10,
        )
        col_combo.pack(side="left", padx=(0, 4))
        col_combo.bind("<<ComboboxSelected>>", self._on_col_changed)

        # 筛选下拉（仅在存在勾选列时显示）
        if self._checkbox_key is not None:
            self._filter_var = tk.StringVar(value=_FILTER_ALL)
            filter_combo = ttk.Combobox(
                bar, textvariable=self._filter_var,
                values=[_FILTER_ALL, _FILTER_CHECKED, _FILTER_UNCHECKED],
                state="readonly", width=8,
            )
            filter_combo.pack(side="left", padx=(0, 4))
            filter_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        # 正则勾选框
        self._regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar, text="正则", variable=self._regex_var,
            command=self._on_regex_changed,
        ).pack(side="left", padx=(0, 6))

        # 计数
        self._match_label_var = tk.StringVar(value="")
        ttk.Label(
            bar, textvariable=self._match_label_var,
            foreground="#666", width=10, anchor="e",
        ).pack(side="left")

    def _column_values(self) -> list[str]:
        """列选择下拉的选项：全部列 + 各可搜索列（排除勾选列）。"""
        values = [_COL_ALL]
        for c in self._columns:
            if c.header and c.searchable and c.kind != "checkbox":
                values.append(c.header)
        return values

    def _build_tree(self) -> None:
        self.tree = ttk.Treeview(
            self, columns=[c.key for c in self._columns],
            show="headings", height=self._height,
        )
        for c in self._columns:
            self.tree.heading(
                c.key, text=c.header,
                command=lambda key=c.key: self._on_header_click(key),
            )
            self.tree.column(
                c.key, width=c.width, anchor=c.anchor,
                stretch=c.stretch, minwidth=40,
            )

        vbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        if self._checkbox_key is not None:
            self.tree.bind("<Button-1>", self._on_tree_click, add="+")
            self._update_headers()

    # ============================================================
    # 数据操作
    # ============================================================
    def clear(self) -> None:
        self._original_data.clear()
        self._visible_data.clear()
        self._data_by_iid.clear()
        self._selected.clear()
        self._sort_column = None
        self._sort_direction = "original"
        self._filter_text = ""
        if self._search_var is not None:
            self._search_var.set("")
        if self._col_var is not None:
            self._col_var.set(_COL_ALL)
        if self._filter_var is not None:
            self._filter_var.set(_FILTER_ALL)
        if self._regex_var is not None:
            self._regex_var.set(False)
        self.tree.delete(*self.tree.get_children())
        self._update_headers()
        self._update_match_count()
        self._notify_selection()

    def set_data(self, rows: list[Any], *, select_all: bool = True) -> None:
        self._original_data = list(rows)
        if self._checkbox_key is not None:
            if select_all:
                self._selected = {self._key(d) for d in rows}
            else:
                self._selected = set()
        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()
        self._update_headers()
        self._notify_selection()

    def append_row(self, data: Any, *, scroll: bool = True,
                   select: Optional[bool] = None) -> None:
        """追加一行。

        select: 勾选列的初始状态。None 时沿用"默认全选"（如果开了勾选列）。
        """
        self._original_data.append(data)
        if self._checkbox_key is not None and select is not False:
            self._selected.add(self._key(data))

        if self._filter_text or self._sort_direction != "original":
            if self._filter_text and not self._match(data, self._filter_text):
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
            tags = self._compose_tags(data)
            self.tree.item(iid, values=values, tags=tags)

    def see_row(self, data: Any) -> None:
        iid = self._key(data)
        if self.tree.exists(iid):
            self.tree.see(iid)

    # ============================================================
    # 勾选
    # ============================================================
    def is_selected(self, data: Any) -> bool:
        return self._key(data) in self._selected

    def get_selected(self) -> list[Any]:
        """返回已勾选的数据列表（按原始顺序）。"""
        return [d for d in self._original_data
                if self._key(d) in self._selected]

    def get_selected_visible(self) -> list[Any]:
        """返回当前可见行中已勾选的数据列表。"""
        return [d for d in self._visible_data
                if self._key(d) in self._selected]

    def set_selected(self, data: Any, selected: bool) -> None:
        iid = self._key(data)
        if selected:
            self._selected.add(iid)
        else:
            self._selected.discard(iid)
        self._refresh_display(iid)
        self._update_headers()
        self._notify_selection()

    def set_all_selected(self, selected: bool) -> None:
        """对所有数据设置勾选状态。"""
        if selected:
            self._selected = {self._key(d) for d in self._original_data}
        else:
            self._selected.clear()
        self._redraw()
        self._update_match_count()
        self._update_headers()

    def select_all_visible(self) -> None:
        """把当前可见的行全部勾选。"""
        for d in self._visible_data:
            self._selected.add(self._key(d))
        self._redraw()
        self._update_headers()
        self._notify_selection()

    def deselect_all_visible(self) -> None:
        for d in self._visible_data:
            self._selected.discard(self._key(d))
        self._redraw()
        self._update_headers()
        self._notify_selection()

    # ---------- 点击处理 ----------
    def _on_tree_click(self, event) -> Optional[str]:
        # 判断是不是点在勾选列上
        col_id = self.tree.identify_column(event.x)
        if not col_id:
            return None
        try:
            idx = int(col_id[1:]) - 1
        except ValueError:
            return None
        cols = list(self.tree["columns"])
        if idx < 0 or idx >= len(cols):
            return None
        clicked_key = cols[idx]
        if clicked_key != self._checkbox_key:
            return None

        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return None

        iid = self.tree.identify_row(event.y)
        if not iid:
            return None

        # 切换这一行的勾选状态
        if iid in self._selected:
            self._selected.discard(iid)
        else:
            self._selected.add(iid)
        self._refresh_display(iid)
        self._update_headers()
        self._notify_selection()
        return "break"      # 阻止默认行选择，让复选框表现得像"复选框"

    def _notify_selection(self) -> None:
        if self._on_selection_changed is not None:
            try:
                self._on_selection_changed()
            except Exception:
                pass

    def _refresh_display(self, iid: str) -> None:
        data = self._data_by_iid.get(iid)
        if data is None:
            return
        values = tuple(self._render_cell(c, data) for c in self._columns)
        tags = self._compose_tags(data)
        self.tree.item(iid, values=values, tags=tags)

    def _compose_tags(self, data: Any) -> tuple:
        base = list(self._row_tags(data))
        if (self._checkbox_key is not None
                and self._key(data) in self._selected):
            base.append("__checked__")
        return tuple(base)

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
        col = self._columns_by_key.get(key)
        if col is None:
            return
        if col.kind == "checkbox":
            self._toggle_all_visible()
            return
        if not col.sortable:
            return

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

    def _toggle_all_visible(self) -> None:
        if not self._visible_data:
            return
        visible_iids = [self._key(d) for d in self._visible_data]
        all_checked = all(iid in self._selected for iid in visible_iids)
        if all_checked:
            for iid in visible_iids:
                self._selected.discard(iid)
        else:
            for iid in visible_iids:
                self._selected.add(iid)
        self._redraw()
        self._update_headers()
        self._notify_selection()

    def _apply_filter_and_sort(self) -> None:
        base = list(self._original_data)

        # 1. 勾选筛选
        if self._filter_var is not None:
            mode = self._filter_var.get()
            if mode == _FILTER_CHECKED:
                base = [d for d in base if self._key(d) in self._selected]
            elif mode == _FILTER_UNCHECKED:
                base = [d for d in base if self._key(d) not in self._selected]

        # 2. 文本搜索
        if self._filter_text:
            base = [d for d in base if self._match(d, self._filter_text)]

        # 3. 排序
        if self._sort_direction == "original" or self._sort_column is None:
            self._visible_data = base
        else:
            col = self._columns_by_key[self._sort_column]
            sort_fn = col.sort if col.sort is not None else col.display
            reverse = (self._sort_direction == "desc")
            self._visible_data = sorted(base, key=sort_fn, reverse=reverse)

    def _update_headers(self) -> None:
        for c in self._columns:
            if c.kind == "checkbox":
                self.tree.heading(c.key, text=self._header_check_symbol())
                continue
            text = c.header
            if (c.sortable and c.key == self._sort_column
                    and self._sort_direction != "original"):
                text += " ▼" if self._sort_direction == "desc" else " ▲"
            self.tree.heading(c.key, text=text)

    def _header_check_symbol(self) -> str:
        visible_iids = [self._key(d) for d in self._visible_data]
        total = len(visible_iids)
        if total == 0:
            return _SYM_UNCHECKED
        checked = sum(1 for iid in visible_iids if iid in self._selected)
        if checked == 0:
            return _SYM_UNCHECKED
        if checked == total:
            return _SYM_CHECKED
        return _SYM_PARTIAL

    # ============================================================
    # 搜索
    # ============================================================
    def _on_search_changed(self, *_args) -> None:
        if self._search_var is None:
            return
        self._filter_text = self._search_var.get()
        self._refresh()

    def _refresh(self) -> None:
        self._apply_filter_and_sort()
        self._redraw()
        self._update_match_count()
        self._update_headers()

    def _on_col_changed(self, _e=None) -> None:
        self._refresh()

    def _on_filter_changed(self, _e=None) -> None:
        self._refresh()

    def _on_regex_changed(self) -> None:
        self._refresh()

    def _match(self, data: Any, keyword: str) -> bool:
        if self._custom_filter_func is not None:
            return bool(self._custom_filter_func(data, keyword))

        col_mode = self._col_var.get() if self._col_var is not None else _COL_ALL
        use_regex = bool(self._regex_var.get()) if self._regex_var is not None else False

        # 决定搜索哪些列
        if col_mode == _COL_ALL:
            cols = [
                c for c in self._columns
                if c.kind != "checkbox" and c.searchable
            ]
        else:
            col = next(
                (c for c in self._columns
                 if c.header == col_mode and c.searchable and c.kind != "checkbox"),
                None,
            )
            cols = [col] if col is not None else []

        if not cols:
            return False

        if use_regex:
            try:
                pat = re.compile(keyword, re.IGNORECASE)
            except re.error:
                return False
            return any(pat.search(self._render_cell(c, data)) for c in cols)

        kw = keyword.lower()
        return any(kw in self._render_cell(c, data).lower() for c in cols)

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
        tags = self._compose_tags(data)
        iid = self._key(data)
        self.tree.insert("", "end", iid=iid, values=values, tags=tags)
        self._data_by_iid[iid] = data

    def _render_cell(self, col: Column, data: Any) -> str:
        if col.kind == "checkbox":
            iid = self._key(data)
            return _SYM_CHECKED if iid in self._selected else _SYM_UNCHECKED
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
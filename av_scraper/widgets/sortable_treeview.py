"""带排序功能的 Treeview 组件。

设计目标：
- 数据是"黑盒对象"，组件不感知其结构
- 通过 Column.display / Column.sort 回调把数据翻译成显示值和排序键
- 点击表头在 升序 → 降序 → 原始顺序 三态之间循环
- append / set_data / clear 时保持当前排序

未来扩展预留（当前接口下可以平滑加入）：
- 搜索 / 筛选：在 _apply_sort 之前插入过滤步骤，维护 _filtered_data
- 复制：绑定 Ctrl+C，取 tree.selection() 对应的数据
- 勾选框：给 Column 增加 checkbox 类型，用 iid 跟踪选中集合
- 导出：遍历 visible_data 调 display 即可
"""

from __future__ import annotations

from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Literal, Optional

_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]

@dataclass
class Column:
    key: str                                   # 内部标识，也是 Treeview 列名
    header: str                                # 表头显示文字
    width: int                                 # 初始宽度
    display: Callable[[Any], str]              # 数据对象 → 显示字符串
    sort: Optional[Callable[[Any], Any]] = None  # 数据对象 → 排序键；None 时用 display
    anchor: _AnchorT = "w"                          # "w" / "center" / "e"
    stretch: bool = False                      # 是否随窗口拉伸
    sortable: bool = True                      # 该列是否允许点击排序


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
        on_sort_changed: Optional[Callable[[Optional[str], str], None]] = None,
    ):
        super().__init__(parent)
        self._columns = list(columns)
        self._columns_by_key = {c.key: c for c in columns}
        self._row_tags = row_tags or (lambda _d: ())
        self._key = key or (lambda d: str(id(d)))
        self._data_by_iid: dict[str, Any] = {}
        self._on_sort_changed = on_sort_changed

        # 数据
        self._original_data: list[Any] = []
        self._visible_data: list[Any] = []

        # 排序状态
        self._sort_column: Optional[str] = None
        self._sort_direction: str = "original"   # "asc" / "desc" / "original"

        # ---- Treeview ----
        self.tree = ttk.Treeview(
            self, columns=[c.key for c in columns],
            show="headings", height=height,
        )
        for c in columns:
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

        for name, opts in (tag_configure or {}).items():
            self.tree.tag_configure(name, **opts)

        # ---- 滚动条 ----
        vbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

    # ============================================================
    # 数据操作
    # ============================================================
    def clear(self) -> None:
        """清空全部数据并重置排序状态。"""
        self._original_data.clear()
        self._visible_data.clear()
        self._data_by_iid.clear()
        self._sort_column = None
        self._sort_direction = "original"
        self.tree.delete(*self.tree.get_children())
        self._update_headers()

    def set_data(self, rows: list[Any]) -> None:
        """整体替换数据。保持当前排序。"""
        self._original_data = list(rows)
        self._apply_sort()
        self._redraw()

    def append_row(self, data: Any, *, scroll: bool = True) -> None:
        """追加一行。保持当前排序。

        scroll=True 时，如果当前是原始顺序，会自动滚动到新行。
        """
        self._original_data.append(data)

        if self._sort_direction == "original":
            self._visible_data.append(data)
            self._insert_one(data)
            if scroll:
                kids = self.tree.get_children()
                if kids:
                    self.tree.see(kids[-1])
        else:
            self._apply_sort()
            self._redraw()

    def update_row(self, data: Any) -> None:
        """按 key 找到对应行并刷新显示。

        排序中也可以调用——iid 是稳定的，位置变了照样能定位。
        """
        iid = self._key(data)
        if not self.tree.exists(iid):
            return
        values = tuple(self._render_cell(c, data) for c in self._columns)
        tags = self._row_tags(data)
        self.tree.item(iid, values=values, tags=tags)
        self._data_by_iid[iid] = data
        # 同步到 _visible_data 里的引用
        for i, d in enumerate(self._visible_data):
            if self._key(d) == iid:
                self._visible_data[i] = data
                break
        # _original_data 里同一对象如果是引用更新，无需处理；
        # 如果是不同对象，找到对应位置替换
        for i, d in enumerate(self._original_data):
            if self._key(d) == iid:
                self._original_data[i] = data
                break

    def see_row(self, data: Any) -> None:
        """滚动到指定行。"""
        iid = self._key(data)
        if self.tree.exists(iid):
            self.tree.see(iid)

    # ============================================================
    # 排序状态
    # ============================================================
    def get_sort_state(self) -> tuple[Optional[str], str]:
        """返回 (排序列 key 或 None, 方向)。"""
        return self._sort_column, self._sort_direction

    def set_sort_state(self, column: Optional[str], direction: str) -> None:
        """外部设置排序状态（供保存/恢复 UI 状态用）。"""
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
        self._apply_sort()
        self._redraw()
        self._update_headers()

    def _on_header_click(self, key: str) -> None:
        if key == self._sort_column:
            if self._sort_direction == "asc":
                self._sort_direction = "desc"
            elif self._sort_direction == "desc":
                self._sort_column = None
                self._sort_direction = "original"
            else:      # original
                self._sort_direction = "asc"
        else:
            self._sort_column = key
            self._sort_direction = "asc"

        self._apply_sort()
        self._redraw()
        self._update_headers()

        if self._on_sort_changed:
            self._on_sort_changed(self._sort_column, self._sort_direction)

    def _apply_sort(self) -> None:
        if self._sort_direction == "original" or self._sort_column is None:
            self._visible_data = list(self._original_data)
            return

        col = self._columns_by_key[self._sort_column]
        sort_fn = col.sort if col.sort is not None else col.display
        reverse = (self._sort_direction == "desc")
        self._visible_data = sorted(
            self._original_data, key=sort_fn, reverse=reverse,
        )

    def _update_headers(self) -> None:
        for c in self._columns:
            if not c.sortable:
                continue
            text = c.header
            if c.key == self._sort_column and self._sort_direction != "original":
                text += " ▼" if self._sort_direction == "desc" else " ▲"
            self.tree.heading(c.key, text=text)

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
        """当前显示顺序的数据列表（副本）。"""
        return list(self._visible_data)

    @property
    def original_data(self) -> list[Any]:
        """原始插入顺序的数据列表（副本）。"""
        return list(self._original_data)
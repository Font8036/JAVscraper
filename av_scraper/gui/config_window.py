"""统一的配置窗口。

设计：
- 每个标签页由 ConfigPage 描述（标题 + 构建/加载/收集/保存/当前/默认 回调）
- 核心配置页由 App 提供，插件通过 Plugin.config_pages() 提供自己的页
- 无插件时窗口只有核心配置页，依然正常工作
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Callable
from .common import set_window_icon

@dataclass
class ConfigPage:
    """配置窗口中的一个标签页。"""
    title: str
    # 在 parent 里构建 UI，返回实际使用的根 Frame
    build: Callable[[ttk.Frame], ttk.Frame]
    # 用配置对象填充 UI
    load: Callable[[Any], None]
    # 从 UI 收集出一个新的配置对象
    collect: Callable[[], Any]
    # 将配置对象写入磁盘（同时更新内存中的引用）
    save: Callable[[Any], None]
    # 返回当前的配置对象（用于"重新加载"）
    current: Callable[[], Any]
    # 返回一个全新的默认配置对象（用于"重置"）
    default: Callable[[], Any]


class ConfigWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, pages: list[ConfigPage]):
        super().__init__(parent)
        # 关键：先隐藏窗口，布局全部完成后再显示，避免左上角闪现
        self.withdraw()
        self.title("配置")
        set_window_icon(self)      # ← 加这一行
        self.geometry("860x640")
        self.minsize(660, 480)

        self._pages: dict[str, ConfigPage] = {}
        self._build(pages)
        self._load_all()

        # 计算居中位置
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2

        # 用完整 geometry（尺寸 + 位置）一次性设置
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        # 显示窗口
        self.deiconify()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ============================================================
    # 构建
    # ============================================================
    def _build(self, pages: list[ConfigPage]) -> None:
        # 按钮先 pack，占据底部，优先获得空间
        btns = ttk.Frame(self, padding=(10, 6, 10, 10))
        btns.pack(fill="x", side="bottom")

        ttk.Button(btns, text="重置", command=self._on_reset).pack(side="left")
        ttk.Button(btns, text="重新加载", command=self._on_reload).pack(
            side="left", padx=(8, 0))

        ttk.Button(btns, text="应用", command=self._on_apply).pack(
            side="right", padx=(4, 0))
        ttk.Button(btns, text="取消", command=self._on_cancel).pack(
            side="right", padx=(4, 0))
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side="right")

        # Notebook 占据剩余空间
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        for page in pages:
            container = ttk.Frame(nb)
            self._build_scrollable_tab(container, page)
            nb.add(container, text=page.title)
            self._pages[page.title] = page

    def _build_scrollable_tab(
        self, container: ttk.Frame, page: ConfigPage,
    ) -> None:
        """在 container 里建一个带垂直滚动条的内容区。"""
        canvas = tk.Canvas(container, highlightthickness=0)
        vbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas, padding=10)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        content = page.build(inner)
        content.pack(fill="both", expand=True)

        def _sync_scrollregion():
            if not inner.winfo_exists():
                return
            content_h = inner.winfo_height()
            canvas_h = canvas.winfo_height()
            canvas.configure(
                scrollregion=(0, 0, inner.winfo_width(),
                              max(content_h, canvas_h)),
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

        def _bind_wheel(_e):
            canvas.bind_all("<MouseWheel>", _on_wheel)

        def _unbind_wheel(_e):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        self.after(50, _sync_scrollregion)

    # ============================================================
    # 加载 / 应用
    # ============================================================
    def _load_all(self) -> None:
        for page in self._pages.values():
            try:
                page.load(page.current())
            except Exception as e:
                messagebox.showerror(
                    "加载失败", f"{page.title}: {e}", parent=self)

    def _apply_all(self) -> list[str]:
        errors: list[str] = []
        for title, page in self._pages.items():
            try:
                new_cfg = page.collect()
                page.save(new_cfg)
            except Exception as e:
                errors.append(f"{title}: {e}")
        return errors

    # ============================================================
    # 按钮
    # ============================================================
    def _on_reset(self) -> None:
        if not messagebox.askyesno(
            "确认重置",
            "恢复所有配置到出厂默认值？\n\n"
            "所有自定义的路径、前缀、开关都会被清空或重置，\n"
            "并立即写回配置文件。\n\n"
            "此操作不可撤销，确定继续？",
            parent=self,
        ):
            return

        errors: list[str] = []
        for title, page in self._pages.items():
            try:
                default = page.default()
                page.load(default)
                page.save(default)
            except Exception as e:
                errors.append(f"{title}: {e}")

        if errors:
            messagebox.showerror("部分重置失败", "\n".join(errors), parent=self)
        else:
            messagebox.showinfo("完成", "已恢复默认配置。", parent=self)

    def _on_reload(self) -> None:
        try:
            for page in self._pages.values():
                page.load(page.current())
        except Exception as e:
            messagebox.showerror("重新加载失败", str(e), parent=self)
            return
        messagebox.showinfo("完成", "已从磁盘重新加载配置。", parent=self)

    def _on_apply(self) -> None:
        errors = self._apply_all()
        if errors:
            messagebox.showerror("部分保存失败", "\n".join(errors), parent=self)
        else:
            messagebox.showinfo("完成", "配置已保存并应用。", parent=self)

    def _on_ok(self) -> None:
        errors = self._apply_all()
        if errors:
            if not messagebox.askyesno(
                "部分保存失败",
                "\n".join(errors) + "\n\n仍然关闭窗口吗？",
                parent=self,
            ):
                return
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()
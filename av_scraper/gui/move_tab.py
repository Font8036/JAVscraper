"""移动选项卡。"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

from ..paths import log_dir
from ..processor import FileProcessor, PlannedOperation
from .common import QueueLogHandler
from ..widgets import HistoryPathInput

_OPS_FILENAME = "move_operations.json"


class MoveTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._q: queue.Queue[Any] = queue.Queue()
        self._working = False
        self._planned: list[PlannedOperation] = []

        self._build()
        self._attach_logger()
        self.sync_from_config()
        self.after(80, self._poll)

    # ---------- 布局 ----------
    def _build(self) -> None:
        row1 = ttk.Frame(self); row1.pack(fill="x", pady=(0, 2))
        ttk.Label(row1, text="输入 JSON:").pack(side="left")
        self.input_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.input_var).pack(
            side="left", padx=4, fill="x", expand=True)
        ttk.Button(row1, text="浏览…", command=self._pick_json).pack(side="left")

        self.target_input = HistoryPathInput(
            self,
            label="目标目录:",
            kind="dir",
            get_value=lambda: (
                self.app.app_config.processor.recent_target_dirs[0]
                if self.app.app_config.processor.recent_target_dirs
                else ""
            ),
            set_value=lambda v: None,   # 目标目录不单独持久化，只记历史
            get_history=lambda: self.app.app_config.processor.recent_target_dirs,
            set_history=lambda h: setattr(
                self.app.app_config.processor, "recent_target_dirs", h),
            get_limit=lambda: self.app.app_config.max_recent_dirs,
            save=lambda: self.app.app_config.save(self.app.config_path),
        )
        self.target_input.pack(fill="x", pady=2)

        row3 = ttk.Frame(self); row3.pack(fill="x", pady=6)
        ttk.Button(row3, text="预览更改", command=self._on_preview).pack(side="left")
        ttk.Button(row3, text="执行移动", command=self._on_execute).pack(
            side="left", padx=4)
        ttk.Button(row3, text="撤回移动", command=self._on_undo).pack(side="left")
        self.stats_var = tk.StringVar(value="尚未预览")
        ttk.Label(row3, textvariable=self.stats_var).pack(side="left", padx=12)
        # 警示：跨盘移动无法暂停
        warn = ttk.Label(
            self,
            text="⚠ 移动开始后无法暂停。请确保源文件与目标目录位于同一硬盘，"
                 "跨盘移动会显著变慢。",
            foreground="#c77b00",
            wraplength=800,
            justify="left",
        )
        warn.pack(fill="x", pady=(0, 6))

        cols = ("src", "dst", "status")
        headers = {"src": "源文件", "dst": "目标路径", "status": "状态"}
        widths = {"src": 380, "dst": 540, "status": 140}
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor="w",
                             stretch=(c == "dst"))
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("skip", foreground="#999999")
        self.tree.tag_configure("overwrite", foreground="#c0392b")
        self.tree.pack(fill="both", expand=True)

        ttk.Label(self, text="日志:").pack(anchor="w", pady=(8, 0))
        self.log = tk.Text(self, height=8, wrap="none", state="disabled")
        self.log.pack(fill="both")

    def _attach_logger(self) -> None:
        handler = QueueLogHandler(self._q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("av_scraper.processor").addHandler(handler)

    # ---------- 外部接口 ----------
    def sync_from_config(self) -> None:
        cfg = self.app.app_config.processor
        if not self.input_var.get():
            self.input_var.set(cfg.input_json or "")
        self.target_input.refresh()

    def set_input_json(self, path: str) -> None:
        self.input_var.set(path)

    # ---------- 事件 ----------
    def _pick_json(self) -> None:
        cur = self.input_var.get()
        init = str(Path(cur).parent) if cur else None
        f = filedialog.askopenfilename(
            initialdir=init, filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if f:
            self.input_var.set(f)

    def _effective_config(self):
        cfg = self.app.app_config.processor
        return replace(
            cfg,
            input_json=self.input_var.get().strip() or cfg.input_json,
            target_directory=self.target_input.get() or cfg.target_directory,
        )

    def _on_preview(self) -> None:
        if self._working:
            return
        json_path = self.input_var.get().strip()
        if not json_path or not Path(json_path).exists():
            messagebox.showerror("错误", f"找不到 JSON 文件：{json_path}")
            return

        self.tree.delete(*self.tree.get_children())
        self._clear_log()
        self._planned = []
        self.app.set_status("预览中…")
        self._working = True
        threading.Thread(target=self._preview_worker,
                         args=(json_path,), daemon=True).start()

    def _preview_worker(self, json_path: str) -> None:
        try:
            proc = FileProcessor(self._effective_config())
            results = proc.load_results(json_path)
            planned = proc.plan(results)
            self._q.put(("planned", planned))
        except Exception:
            logging.getLogger("av_scraper.processor").exception("预览失败")
            self._q.put(("planned", None))

    def _on_execute(self) -> None:
        if self._working:
            return
        if not self.input_var.get().strip():
            messagebox.showwarning("提示", "请先指定输入 JSON")
            return
        if not messagebox.askyesno(
            "确认", "确定要执行移动操作吗？这会实际移动文件。"):
            return
        self.stats_var.set("执行中 0/0")
        self._clear_log()
        self.app.set_status("执行中…")
        self._working = True
        threading.Thread(target=self._execute_worker, daemon=True).start()

    def _execute_worker(self) -> None:
        try:
            proc = FileProcessor(self._effective_config())
            results = proc.load_results(self.input_var.get().strip())
            planned = proc.plan(results)

            def on_progress(idx, total, op, status):
                self._q.put(("progress", (idx, total, op, status)))

            ops, success, skipped, failed = proc.execute(
                planned, progress=on_progress,
                )
            if ops:
                ops_file = log_dir() / _OPS_FILENAME
                proc.save_operations(ops, ops_file)
                self._q.put(("log",
                             f"[提示] 移动记录已保存：{ops_file}"))
            self._q.put(("exec_done", (success, skipped, failed)))
        except Exception:
            logging.getLogger("av_scraper.processor").exception("执行失败")
            self._q.put(("exec_done", None))

    def _on_undo(self) -> None:
        if self._working:
            return
        json_path = self.input_var.get().strip()
        if not json_path:
            messagebox.showwarning("提示", "请先指定输入 JSON")
            return
        if not messagebox.askyesno("确认", "确定要撤回上一次的所有移动操作吗？"):
            return
        self._clear_log()
        self.app.set_status("撤回中…")
        self._working = True
        threading.Thread(target=self._undo_worker,
                         args=(json_path,), daemon=True).start()

    def _undo_worker(self, json_path: str) -> None:
        try:
            ops_file = log_dir() / _OPS_FILENAME
            if not ops_file.exists():
                self._q.put(("log", f"[错误] 找不到记录文件：{ops_file}"))
                self._q.put(("undo_done", None))
                return
            proc = FileProcessor(self._effective_config())
            success, failed = proc.undo(ops_file)
            self._q.put(("undo_done", (success, failed)))
        except Exception:
            logging.getLogger("av_scraper.processor").exception("撤回失败")
            self._q.put(("undo_done", None))

    # ---------- 主线程消费 ----------
    def _poll(self) -> None:
        try:
            while True:
                item = self._q.get_nowait()
                if isinstance(item, str):
                    self._append_log(item)
                    continue
                kind, payload = item
                if kind == "log":
                    self._append_log(payload)
                elif kind == "planned":
                    self._show_planned(payload)
                elif kind == "progress":
                    self._on_move_progress(payload)
                elif kind == "exec_done":
                    self._finish_move(payload)
                elif kind == "undo_done":
                    self._finish_undo(payload)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    _STATUS_TEXT = {
        "move": "移动",
        "rename": "重命名后移动",
        "skip": "跳过（已存在）",
        "overwrite": "覆盖",
    }
    _STATUS_TAG = {
        "move": "ok",
        "rename": "ok",
        "skip": "skip",
        "overwrite": "overwrite",
    }

    def _on_move_progress(self, payload) -> None:
        idx, total, op, status = payload
        self.stats_var.set(f"执行中 {idx}/{total}")

        # 状态列文案
        status_text = {
            "ok": "✓ 移动成功",
            "skip": "○ 跳过",
            "missing": "✗ 源文件不存在",
        }.get(status)

        if status_text is None and status.startswith("error:"):
            status_text = "✗ " + status[len("error:"):]

        # 高亮当前行
        selected = self.tree.selection()
        for iid in selected:
            self.tree.selection_remove(iid)

    def _show_planned(self, planned: Optional[list[PlannedOperation]]) -> None:
        self._working = False
        if planned is None:
            self.stats_var.set("预览失败")
            self.app.set_status("就绪")
            return
        self._planned = planned
        for p in planned:
            self.tree.insert(
                "", "end",
                values=(str(p.src), str(p.dst),
                        self._STATUS_TEXT.get(p.status, p.status)),
                tags=(self._STATUS_TAG.get(p.status, "ok"),),
            )
        self.stats_var.set(f"共 {len(planned)} 个操作")
        self.app.set_status("预览完成")

    def _finish_move(self, payload: Optional[tuple[int, int, int]]) -> None:
        self._working = False
        self.app.set_status("就绪")
        if payload is None:
            messagebox.showerror("移动", "移动失败，请查看日志。")
            return
        success, skipped, failed = payload
        if success > 0:
            self.target_input.commit()
        msg = f"成功 {success}，跳过 {skipped}，失败 {failed}"
        self.stats_var.set(msg)          # ← 新增：把进度覆盖为最终统计
        messagebox.showinfo(
            "移动完成",
            msg,
        )

    def _finish_undo(self, payload: Optional[tuple[int, int]]) -> None:
        self._working = False
        self.app.set_status("就绪")
        if payload is None:
            messagebox.showerror("撤回", "撤回失败，请查看日志。")
            return
        success, failed = payload
        msg = f"成功 {success}，失败 {failed}"
        self.stats_var.set(msg)          # ← 新增
        messagebox.showinfo("撤回完成", msg)

    # ---------- 日志 ----------
    def _append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
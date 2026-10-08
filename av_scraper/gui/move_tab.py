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
from ..processor import (
    FileProcessor,
    MoveOperation,
    PlannedOperation,
    find_target_collisions,
)
from .common import QueueLogHandler
from ..widgets import HistoryPathInput, Column, SortableTreeview

_OPS_FILENAME = "move_operations.json"
_LABEL_WIDTH = 10          # 左列标签的统一宽度，和配置页风格一致


def _short(path: Path) -> str:
    """文件在弹窗/日志里的短标识：上级目录名/文件名，便于区分同名文件。"""
    return f"{path.parent.name}/{path.name}" if path.parent.name else path.name


def build_collision_message(collisions: dict[str, list[PlannedOperation]]) -> str:
    """预览阶段"批内重名"提醒的文案。"""
    groups = list(collisions.items())
    shown, rest = groups[:5], groups[5:]
    lines = [
        f"目标：{Path(dst).name}\n   来自：" + "、".join(_short(p.src) for p in ops)
        for dst, ops in shown
    ]
    if rest:
        lines.append(f"……另有 {len(rest)} 处冲突未列出")

    files = sum(len(ops) for ops in collisions.values())
    return (
        f"检测到本批有 {files} 个文件会写到同样的目标路径（共 {len(groups)} 处）：\n\n"
        + "\n".join(lines)
        + "\n\n如果「配置 → 文件冲突处理」当前是「覆盖」，"
          "先移入的那个文件会被后一个覆盖删除，且无法找回。\n"
          "建议：把冲突处理改成「重命名」，或者只勾选其中一个文件再执行。"
    )


_MOVE_SKIP_TEXT = {
    "skip": "跳过（目标已存在）",
    "missing": "跳过（源文件不存在）",
}


def move_status_text(op: PlannedOperation, status: str) -> str:
    """移动过程中值得写进日志的一行；正常完成返回空串。"""
    if status == "ok":
        return ""
    if status.startswith("error:"):
        return f"[失败] {op.src.name} → {op.dst}：{status[len('error:'):]}"
    return f"[{_MOVE_SKIP_TEXT.get(status, status)}] {op.src.name}"


def undo_status_text(op: MoveOperation, status: str) -> str:
    """撤回过程中值得写进日志的一行；正常完成返回空串。"""
    if status == "ok":
        return ""
    if status == "missing":
        return f"[失败] {op.original_filename}：目标位置已找不到该文件，无法撤回"
    if status == "occupied":
        return (f"[失败] {op.original_filename}：原路径已有同名文件，"
                f"已跳过以免覆盖（{op.original_path}）")
    if status.startswith("error:"):
        return f"[失败] {op.original_filename}：{status[len('error:'):]}"
    return f"[失败] {op.original_filename}：{status}"


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
        ttk.Label(
            row1, text="输入 JSON:",
            width=_LABEL_WIDTH, anchor="e",
        ).pack(side="left")
        self.input_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.input_var).pack(
            side="left", padx=4, fill="x", expand=True)
        ttk.Button(row1, text="浏览…", command=self._pick_json).pack(side="left")

        self.target_input = HistoryPathInput(
            self,
            label="目标目录:",
            kind="dir",
            label_width=_LABEL_WIDTH,     # ← 新增
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

        # 状态映射
        def _status_text(op) -> str:
            return {
                "move": "移动",
                "rename": "重命名后移动",
                "skip": "跳过（已存在）",
                "overwrite": "覆盖",
            }.get(op.status, op.status) or ""

        def _status_rank(op):
            # 数值越大越"需要注意"，排序时能直观分组
            return {
                "move": 0,
                "rename": 0,
                "overwrite": 2,
                "skip": 1,
            }.get(op.status, 0)

        def _tag(op):
            if op.status in ("move", "rename"):
                return ("ok",)
            if op.status == "skip":
                return ("skip",)
            if op.status == "overwrite":
                return ("overwrite",)
            return ()

        self.result_tree = SortableTreeview(
            self,
            height=self.app.app_config.table_height,
            columns=[
                Column("__check__", "", 40,
                       kind="checkbox", anchor="center",
                       sortable=False, searchable=False),
                Column("code", "番号", 130,
                       display=lambda op: op.extracted_code,
                       sort=lambda op: op.extracted_code.lower()),
                Column("src", "源文件", 320,
                       display=lambda op: str(op.src),
                       sort=lambda op: str(op.src).lower()),
                Column("dst", "目标路径", 460,
                       display=lambda op: str(op.dst),
                       sort=lambda op: str(op.dst).lower(),
                       stretch=True),
                Column("status", "状态", 140,
                       display=_status_text,
                       sort=_status_rank),
            ],
            searchable=True,          # ← 新增
            on_selection_changed=self._on_selection_changed,      # ← 新增
            row_tags=_tag,
            tag_configure={
                "ok":        {"foreground": "#1a7f37"},
                "skip":      {"foreground": "#999999"},
                "overwrite": {"foreground": "#c0392b"},
            },
        )
        self.result_tree.pack(fill="both", expand=True)

        ttk.Label(self, text="日志:").pack(anchor="w", pady=(8, 0))
        self.log = tk.Text(self, height=self.app.app_config.log_height, wrap="none", state="disabled")
        self.log.pack(fill="both")

    def _attach_logger(self) -> None:
        if getattr(self, "_logger_attached", False):
            return
        self._logger_attached = True

        handler = QueueLogHandler(self._q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("av_scraper.processor").addHandler(handler)

    def apply_layout(self) -> None:
        cfg = self.app.app_config
        if self.result_tree is not None:
            self.result_tree.tree.configure(height=cfg.table_height)
        if self.log is not None:
            self.log.configure(height=cfg.log_height)

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

        self.result_tree.clear()
        self._clear_log()
        self._planned = []
        self.app.set_status("预览中…")
        self._working = True
        threading.Thread(target=self._preview_worker,
                         args=(json_path,), daemon=True).start()

    def _preview_worker(self, json_path: str) -> None:
        try:
            proc = self._make_processor() 
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

        # 只处理勾选的行（已按当前预览构建）
        selected = self.result_tree.get_selected()
        if not selected:
            messagebox.showwarning(
                "提示", "没有勾选任何文件。请先预览并勾选要移动的行。")
            return

        if not messagebox.askyesno(
            "确认",
            f"确定要执行移动操作吗？\n\n将移动 {len(selected)} 个文件。",
        ):
            return

        self.stats_var.set(f"执行中 0/{len(selected)}")
        self._clear_log()
        self.app.set_status("执行中…")
        self._working = True

        # 把勾选后的 PlannedOperation 列表直接传给 worker，
        # 不再重新 load + plan，保证"执行的"= "预览时看到的"
        threading.Thread(
            target=self._execute_worker, args=(selected,), daemon=True,
        ).start()

    def _execute_worker(self, planned: list[PlannedOperation]) -> None:
        try:
            proc = self._make_processor() 

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
            proc = self._make_processor()

            def on_progress(op, status):
                text = undo_status_text(op, status)
                if text:
                    self._q.put(("log", text))

            success, failed = proc.undo(ops_file, progress=on_progress)
            self._q.put(("undo_done", (success, failed)))
        except Exception:
            logging.getLogger("av_scraper.processor").exception("撤回失败")
            self._q.put(("undo_done", None))

    def _make_processor(self) -> FileProcessor:
        scraper_cfg = self.app.app_config.scraper
        return FileProcessor(
            self._effective_config(),
            video_exts=scraper_cfg.video_extensions,
            attachment_exts=scraper_cfg.attachment_extensions,
        )

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

    def _on_move_progress(self, payload) -> None:
        idx, total, op, status = payload
        self.stats_var.set(f"执行中 {idx}/{total}")
        text = move_status_text(op, status)
        if text:
            self._append_log(text)

    def _on_selection_changed(self) -> None:
        total = len(self._planned)
        if total == 0:
            return
        selected = len(self.result_tree.get_selected())
        self.stats_var.set(f"共 {total} 个操作，已勾选 {selected} 个")

    def _show_planned(self, planned: Optional[list[PlannedOperation]]) -> None:
        self._working = False
        if planned is None:
            self.stats_var.set("预览失败")
            self.app.set_status("就绪")
            return
        self._planned = planned
        self.result_tree.set_data(planned)
        # 用统一格式（set_data 里触发 on_selection_changed 时也走同一个回调）
        self._on_selection_changed()
        self.app.set_status("预览完成")
        self._warn_target_collisions(planned)

    def _warn_target_collisions(self, planned: list[PlannedOperation]) -> None:
        """预览后提醒：本批有文件会写到同一个目标路径（"覆盖"时会丢文件）。"""
        collisions = find_target_collisions(planned)
        if not collisions:
            return
        self._append_log(f"[提醒] 本批有 {len(collisions)} 处目标路径冲突，详见弹窗")
        messagebox.showwarning(
            "注意：本批有文件会互相覆盖",
            build_collision_message(collisions),
            parent=self,
        )

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
        if failed > 0:
            messagebox.showwarning(
                "移动完成（有失败）",
                msg + "\n\n失败或跳过的原因见下方日志。",
                parent=self,
            )
        else:
            messagebox.showinfo("移动完成", msg, parent=self)

    def _finish_undo(self, payload: Optional[tuple[int, int]]) -> None:
        self._working = False
        self.app.set_status("就绪")
        if payload is None:
            messagebox.showerror("撤回", "撤回失败，请查看日志。")
            return
        success, failed = payload
        msg = f"成功 {success}，失败 {failed}"
        self.stats_var.set(msg)          # ← 新增
        if failed > 0:
            messagebox.showwarning(
                "撤回完成（有失败）",
                msg + "\n\n失败原因见下方日志。\n"
                      "移动记录已保留，处理完冲突后可以再点一次「撤回移动」。",
                parent=self,
            )
        else:
            messagebox.showinfo("撤回完成", msg, parent=self)

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
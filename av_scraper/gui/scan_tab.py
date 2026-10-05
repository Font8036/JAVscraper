"""扫描选项卡。"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Optional

from ..paths import log_dir
from ..config import ScraperConfig
from ..reports import make_run_directory, save_csv, save_json, save_text_report
from ..scraper import CodeExtractor, ScrapeResult
from .common import QueueLogHandler, human_size
from ..widgets import Column, HistoryPathInput, SortableTreeview

class ScanTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._q: queue.Queue[Any] = queue.Queue()
        self._scanning = False
        self._results: list[ScrapeResult] = []
        self._total_files = 0          # ← 新增
        self._scanned_count = 0        # ← 新增
        self._build()
        self._attach_logger()
        self.sync_from_config()
        self.after(80, self._poll)

    # ---------- 布局 ----------
    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.dir_input = HistoryPathInput(
            top,
            label="扫描目录:",
            kind="dir",
            get_value=lambda: (
                self.app.app_config.scraper.recent_scan_dirs[0]
                if self.app.app_config.scraper.recent_scan_dirs
                else ""
            ),
            set_value=lambda v: None,   # 扫描目录的当前值不单独持久化，只记历史
            get_history=lambda: self.app.app_config.scraper.recent_scan_dirs,
            set_history=lambda h: setattr(
                self.app.app_config.scraper, "recent_scan_dirs", h),
            get_limit=lambda: self.app.app_config.max_recent_dirs,
            save=lambda: self.app.app_config.save(self.app.config_path),
        )
        self.dir_input.pack(side="left", fill="x", expand=True, padx=4)

        # 状态判定辅助（把 ScrapeResult 翻译成展示/排序信息）
        def _status_rank(r):
            if r.inherited:
                return 2
            return 1 if r.is_extracted else 0

        def _status_text(r):
            if r.inherited:
                return "从父目录继承"
            return "成功提取" if r.is_extracted else "保持原样"

        def _icon(r):
            if r.inherited:
                return "↳"
            return "✓" if r.is_extracted else "✗"

        def _tag(r):
            if r.inherited:
                return ("inherited",)
            return ("extracted",) if r.is_extracted else ("original",)

        # -------- 按钮行 --------
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=6)
        self.btn_scan = ttk.Button(
            actions, text="开始扫描", command=self._on_scan,
        )
        self.btn_scan.pack(side="left")
        self.stats_var = tk.StringVar(value="尚未扫描")
        ttk.Label(actions, textvariable=self.stats_var).pack(
            side="left", padx=12,
        )

        self.result_tree = SortableTreeview(
            self,
            height=self.app.app_config.table_height,
            columns=[
                Column("icon", "", 40,
                       display=_icon, sort=_status_rank,
                       anchor="center"),
                Column("filename", "文件名", 480,
                       display=lambda r: r.filename,
                       sort=lambda r: r.filename.lower(),
                       stretch=True),
                Column("code", "提取码", 200,
                       display=lambda r: r.extracted_code,
                       sort=lambda r: r.extracted_code.lower()),
                Column("status", "状态", 120,
                       display=_status_text, sort=_status_rank),
                Column("size", "大小", 100,
                       display=lambda r: human_size(r.file_size),
                       sort=lambda r: r.file_size),
            ],
            searchable=True,          # ← 新增
            row_tags=_tag,
            tag_configure={
                "extracted": {"foreground": "#1a7f37"},
                "original":  {"foreground": "#999999"},
                "inherited": {"foreground": "#0a58ca"},
            },
        )
        self.result_tree.pack(fill="both", expand=True, pady=(8, 0))

        ttk.Label(self, text="日志:").pack(anchor="w", pady=(6, 0))
        self.log = tk.Text(self, height=self.app.app_config.log_height, wrap="none", state="disabled")
        self.log.pack(fill="both")

    def _attach_logger(self) -> None:
        if getattr(self, "_logger_attached", False):
            return
        self._logger_attached = True

        handler = QueueLogHandler(self._q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for name in ("av_scraper.scraper", "av_scraper.reports"):
            logging.getLogger(name).addHandler(handler)

    def apply_layout(self) -> None:
        cfg = self.app.app_config
        if self.result_tree is not None:
            self.result_tree.tree.configure(height=cfg.table_height)
        if self.log is not None:
            self.log.configure(height=cfg.log_height)

    # ---------- 外部接口 ----------
    def sync_from_config(self) -> None:
        self.dir_input.refresh()

    # ---------- 事件 ----------

    def _on_scan(self) -> None:
        if self._scanning:
            return
        directory = self.dir_input.get()
        if not directory:
            messagebox.showwarning("提示", "请先选择扫描目录")
            return
        if not Path(directory).exists():
            messagebox.showerror("错误", f"目录不存在：{directory}")
            return

        self.result_tree.clear()
        self._clear_log()
        self._results = []
        self.stats_var.set("正在统计文件数…")
        self.app.set_status("正在扫描…")
        self._scanning = True
        self.btn_scan.configure(state="disabled")

        cfg = self.app.app_config.scraper
        threading.Thread(
            target=self._worker, args=(directory, cfg), daemon=True,
        ).start()

    # ---------- 后台 ----------
    def _worker(self, directory: str, cfg: ScraperConfig) -> None:
        try:
            extractor = CodeExtractor(cfg)
            results = extractor.scan_directory(
                directory,
                recursive=cfg.recursive_processing,
                progress=lambda r: self._q.put(("row", r)),
                on_total=lambda n: self._q.put(("total", n)),   # ← 新增
            )
            
            base = cfg.output_directory or str(log_dir())
            run_dir = make_run_directory(base)
            save_json(results, run_dir / cfg.output_filename)
            save_text_report(results, run_dir / "extraction_report.txt")
            save_csv(results, run_dir / "extraction_table.csv")

            self._q.put(("json", str(run_dir / cfg.output_filename)))
            self._q.put(("done", results))
        except Exception as e:
            logging.getLogger("av_scraper.scraper").exception("扫描失败")
            self._q.put(("log_error", f"扫描失败：{e}"))
            self._q.put(("done", None))

    # ---------- 主线程消费 ----------
    def _poll(self) -> None:
        try:
            while True:
                item = self._q.get_nowait()
                if isinstance(item, str):
                    self._append_log(item)
                    continue
                kind, payload = item
                if kind == "total":
                    self._on_total(payload)
                elif kind == "row":
                    self._insert_row(payload)
                    self._scanned_count += 1
                    self._update_progress()
                elif kind == "json":
                    self.app.move_tab.set_input_json(payload)
                    self._append_log(f"[提示] 结果已同步到移动页：{payload}")
                elif kind == "log_error":
                    self._append_log(payload)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _on_total(self, total: int) -> None:
        self._total_files = total
        self._scanned_count = 0
        self._update_progress()

    def _update_progress(self) -> None:
        total = self._total_files
        scanned = self._scanned_count
        if total > 0:
            pct = scanned / total * 100
            self.stats_var.set(f"扫描中 {scanned}/{total}（{pct:.1f}%）")
        else:
            self.stats_var.set("未发现可扫描的文件")

    def _insert_row(self, r: ScrapeResult) -> None:
        self.result_tree.append_row(r)

    def _finish(self, results: Optional[list[ScrapeResult]]) -> None:
        self._scanning = False
        self.btn_scan.configure(state="normal")
        if results is None:
            self.stats_var.set("扫描失败")
            self.app.set_status("就绪")
            return

        self._results = results
        
        # —— 记住本次使用的目录 ——
        self.dir_input.commit()

        total = len(results)
        extracted = sum(1 for r in results if r.is_extracted and not r.inherited)
        inherited = sum(1 for r in results if r.inherited)
        ratio = (extracted + inherited) / total * 100 if total else 0
        parts = [f"共 {total} 个文件"]
        if extracted:
            parts.append(f"直接提取 {extracted}")
        if inherited:
            parts.append(f"父目录继承 {inherited}")
        parts.append(f"总成功率 {ratio:.1f}%")
        self.stats_var.set("，".join(parts))
        self.app.set_status("扫描完成")

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
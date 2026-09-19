"""扫描选项卡。"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

from ..paths import log_dir
from ..config import ScraperConfig
from ..reports import make_run_directory, save_csv, save_json, save_text_report
from ..scraper import CodeExtractor, ScrapeResult
from .common import QueueLogHandler, human_size, make_dir_combobox, remember_dir


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
        ttk.Label(top, text="扫描目录:").pack(side="left")
        self.dir_var = tk.StringVar()
        self.dir_combo = make_dir_combobox(top, self.dir_var)
        self.dir_combo.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(top, text="浏览…", command=self._pick_dir).pack(side="left")
        self.btn_scan = ttk.Button(top, text="开始扫描", command=self._on_scan)
        self.btn_scan.pack(side="left", padx=4)

        # ---------- 表头元数据升成实例属性 ----------
        self._HEADERS = {
            "icon":     "",
            "filename": "文件名",
            "code":     "提取码",
            "status":   "状态",
            "size":     "大小",
        }
        self._WIDTHS = {
            "icon": 40, "filename": 480, "code": 200, "status": 100, "size": 100,
        }
        # 每一列的排序 key：返回可比较的值
        self._SORT_KEYS = {
            "icon":     lambda r: 1 if r.is_extracted else 0,   # 0=✗, 1=✓
            "status":   lambda r: 1 if r.is_extracted else 0,
            "filename": lambda r: r.filename.lower(),
            "code":     lambda r: r.extracted_code.lower(),
            "size":     lambda r: r.file_size,
        }
        # col -> 是否降序；只保留一个活动排序列
        self._sort_state: dict[str, bool] = {}

        cols = ("icon", "filename", "code", "status", "size")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for c in cols:
            self.tree.heading(
                c, text=self._HEADERS[c],
                command=lambda col=c: self._sort_by(col),
            )
            self.tree.column(
                c, width=self._WIDTHS[c],
                anchor="center" if c == "icon" else "w",
                stretch=(c == "filename"),
            )
        self.tree.tag_configure("extracted", foreground="#1a7f37")
        self.tree.tag_configure("original", foreground="#999999")
        self.tree.pack(fill="both", expand=True, pady=(8, 0))

        self.stats_var = tk.StringVar(value="尚未扫描")
        ttk.Label(self, textvariable=self.stats_var).pack(anchor="w", pady=4)

        ttk.Label(self, text="日志:").pack(anchor="w")
        self.log = tk.Text(self, height=8, wrap="none", state="disabled")
        self.log.pack(fill="both")

    # ---------- 排序 ----------
    def _sort_by(self, col: str) -> None:
        """点击表头切换排序列与方向。"""
        if self._scanning or not self._results:
            return

        # 首次点某列：升序；再次点同一列：反转
        if col in self._sort_state:
            reverse = not self._sort_state[col]
        else:
            reverse = False

        self._results.sort(key=self._SORT_KEYS[col], reverse=reverse)

        # 只保留一个活动排序列；避免多列状态叠加造成歧义
        self._sort_state = {col: reverse}

        self._redraw()
        self._update_headers()

    def _update_headers(self) -> None:
        active = next(iter(self._sort_state), None)
        reverse = self._sort_state.get(active, False) if active else False
        for c, base in self._HEADERS.items():
            text = base
            if c == active:
                text += " ▼" if reverse else " ▲"
            self.tree.heading(c, text=text)

    def _redraw(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for r in self._results:
            self._insert_row(r)

    def _attach_logger(self) -> None:
        handler = QueueLogHandler(self._q)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for name in ("av_scraper.scraper", "av_scraper.reports"):
            logging.getLogger(name).addHandler(handler)

    # ---------- 外部接口 ----------
    def sync_from_config(self) -> None:
        cfg = self.app.app_config.scraper
        self.dir_combo.configure(values=list(cfg.recent_scan_dirs))
        if not self.dir_var.get():
            # 优先用最近一次用过的，其次用配置里的默认目录
            last = cfg.recent_scan_dirs[0] if cfg.recent_scan_dirs else ""
            self.dir_var.set(last or cfg.default_directory or "")

    # ---------- 事件 ----------
    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or None)
        if d:
            self.dir_var.set(d)
            self.dir_combo.configure(
                values=list(self.app.app_config.scraper.recent_scan_dirs))

    def _on_scan(self) -> None:
        if self._scanning:
            return
        directory = self.dir_var.get().strip()
        if not directory:
            messagebox.showwarning("提示", "请先选择扫描目录")
            return
        if not Path(directory).exists():
            messagebox.showerror("错误", f"目录不存在：{directory}")
            return

        self.tree.delete(*self.tree.get_children())
        self._clear_log()
        self._results = []
        self._sort_state = {}          # ← 新增
        self._update_headers()          # ← 恢复表头（去掉 ▲▼）
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
        except Exception:
            logging.getLogger("av_scraper.scraper").exception("扫描失败")
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
                elif kind == "json":
                    self.app.move_tab.set_input_json(payload)
                    self._append_log(f"[提示] 结果已同步到移动页：{payload}")
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
        icon = "✓" if r.is_extracted else "✗"
        status = "成功提取" if r.is_extracted else "保持原样"
        tag = "extracted" if r.is_extracted else "original"
        self.tree.insert(
            "", "end",
            values=(icon, r.filename, r.extracted_code, status,
                    human_size(r.file_size)),
            tags=(tag,),
        )
        self._scanned_count += 1
        self._update_progress()

    def _finish(self, results: Optional[list[ScrapeResult]]) -> None:
        self._scanning = False
        self.btn_scan.configure(state="normal")
        if results is None:
            self.stats_var.set("扫描失败")
            self.app.set_status("就绪")
            return

        self._results = results
        
        # —— 记住本次使用的目录 ——
        self._remember_current_dir()

        total = len(results)
        extracted = sum(1 for r in results if r.is_extracted)
        ratio = extracted / total * 100 if total else 0
        self.stats_var.set(
            f"共 {total} 个文件，成功提取 {extracted} 个（{ratio:.1f}%）")
        self.app.set_status("扫描完成")

    def _remember_current_dir(self) -> None:
        cfg = self.app.app_config.scraper
        d = self.dir_var.get().strip()
        if not d:
            return
        cfg.recent_scan_dirs = remember_dir(
            cfg.recent_scan_dirs, d, cfg.max_recent_dirs)
        self.dir_combo.configure(values=list(cfg.recent_scan_dirs))
        try:
            self.app.app_config.save(self.app.config_path)
        except OSError:
            pass

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
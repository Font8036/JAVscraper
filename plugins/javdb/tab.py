"""JavDB 插件的 GUI 入口。

实现核心约定的 Plugin 协议：
- 构造函数接收 PluginContext
- create_tab(notebook) 返回一个 ttk.Frame
"""

from __future__ import annotations

import json
import asyncio
import queue
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional
from dataclasses import dataclass
from av_scraper.widgets import Column, SortableTreeview, ConfigForm
from av_scraper.plugin_api import PluginContext
from .notify import play_beep, show_toast
from .parse import is_matched
from .config import JavdbConfig, normalize_base_url
from .fetch import BrowserSession, load_targets, save_csv, scrape_javdb
from .report import build_excel

@dataclass
class FetchRow:
    idx: int
    target: str
    fanhao: str = ""
    name: str = ""
    status: str = "waiting"    # waiting / running / ok / mismatch / failed / stopped
    message: str = ""

class JavdbPlugin:
    name = "JavDB 刮削"
    version = "1.3"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self._config_path = self._resolve_config_path()
        self._config = JavdbConfig.load(self._config_path)
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._working = False
        self._browser_session: Optional[BrowserSession] = None
        self._root: Optional[ttk.Frame] = None
        self._rows: list[FetchRow] = []
        self._row_by_idx: dict[int, FetchRow] = {}

    # ---------- 配置路径 ----------
    def _resolve_config_path(self) -> Path:
        """插件若被打进 exe，plugin_dir 是只读的临时目录；
        此时把配置写到 exe 同级，用户下次打开还能看到。"""
        meipass = getattr(sys, "_MEIPASS", None)
        plugin_dir = self.ctx.plugin_dir
        if meipass and str(plugin_dir).startswith(str(meipass)):
            return Path(sys.executable).resolve().parent / "javdb_config.json"
        return plugin_dir / "config.json"

    # ---------- 供核心调用 ----------
    def create_tab(self, notebook) -> ttk.Frame:
        root = ttk.Frame(notebook, padding=8)
        self._root = root
        self._build(root)
        self._form.load_from(self._config) 
        root.after(80, self._poll)
        return root

    # ============================================================
    # UI 构建
    # ============================================================
    def _build(self, root: ttk.Frame) -> None:
        # -------- 配置区 --------
        cfg_frame = ttk.LabelFrame(root, text="配置", padding=8)
        cfg_frame.pack(fill="x", pady=(0, 6))

        self._form = ConfigForm(cfg_frame, JavdbConfig)
        self._form.pack(fill="x")

        # 在"登录状态"那一行加"导入浏览器 Cookie"按钮
        state_row = self._form.get_row_frame("state_file")
        if state_row is not None:
            ttk.Button(
                state_row, text="导入浏览器 Cookie",
                command=self._on_import_cookies,
            ).pack(side="left", padx=(4, 0))

        # -------- 按钮区 --------
        actions = ttk.Frame(root, padding=(0, 4))
        actions.pack(fill="x")
        self._btn_save = ttk.Button(
            actions, text="保存配置", command=self._on_save_config,
        )
        self._btn_save.pack(side="left")

        self._btn_open_browser = ttk.Button(
            actions, text="启动浏览器", command=self._on_open_browser)
        self._btn_open_browser.pack(side="left", padx=(8, 0))
        self._btn_close_browser = ttk.Button(
            actions, text="关闭浏览器", command=self._on_close_browser,
            state="disabled")
        self._btn_close_browser.pack(side="left", padx=4)

        self._btn_fetch = ttk.Button(
            actions, text="开始爬取", command=self._on_fetch)
        self._btn_fetch.pack(side="left", padx=(8, 0))
        self._btn_stop = ttk.Button(
            actions, text="停止", command=self._on_stop, state="disabled")
        self._btn_stop.pack(side="left", padx=4)
        self._btn_report = ttk.Button(
            actions, text="生成 Excel", command=self._on_build_excel)
        self._btn_report.pack(side="left", padx=4)
        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(actions, textvariable=self._status_var).pack(
            side="left", padx=12)

        # -------- 进度表格 --------
        def _status_text(r: "FetchRow") -> str:
            if r.status == "waiting":
                return "等待"
            if r.status == "running":
                return "进行中…"
            if r.status == "ok":
                return "✓ 完成"
            if r.status == "mismatch":
                return "⚠ 完成（番号不同）"
            if r.status == "failed":
                return "✗ 失败" + (f"：{r.message[:30]}" if r.message else "")
            if r.status == "stopped":
                return "○ 已停止"
            return r.status

        def _status_rank(r: "FetchRow") -> int:
            # 数值越小越靠前，排序时把"需要关注"的放上面
            return {
                "failed": 0,
                "mismatch": 1,
                "stopped": 2,
                "running": 3,
                "waiting": 4,
                "ok": 5,
            }.get(r.status, 9)

        def _tag(r: "FetchRow") -> tuple:
            return {
                "ok": ("ok",),
                "mismatch": ("warn",),
                "failed": ("failed",),
                "running": ("running",),
                "stopped": ("stopped",),
            }.get(r.status, ())

        self.result_tree = SortableTreeview(
            root,
            key=lambda r: f"row_{r.idx}",
            height=8,
            columns=[
                Column("target", "目标番号", 140,
                       display=lambda r: r.target,
                       sort=lambda r: r.target.lower()),
                Column("fanhao", "实际番号", 140,
                       display=lambda r: r.fanhao or "—",
                       sort=lambda r: (r.fanhao or "").lower()),
                Column("name", "名称", 420,
                       display=lambda r: r.name or "—",
                       sort=lambda r: (r.name or "").lower(),
                       stretch=True),
                Column("status", "状态", 180,
                       display=_status_text,
                       sort=_status_rank),
            ],
            searchable=True,          # ← 新增
            row_tags=_tag,
            tag_configure={
                "ok":        {"foreground": "#1a7f37"},
                "running":   {"foreground": "#0a58ca"},
                "warn":      {"foreground": "#c77b00"},
                "failed":    {"foreground": "#c0392b"},
                "stopped":   {"foreground": "#999999"},
            },
        )
        self.result_tree.pack(fill="both", expand=True, pady=(8, 0))

        # -------- 日志 --------
        ttk.Label(root, text="日志:").pack(anchor="w", pady=(6, 0))
        self._log = tk.Text(root, height=8, wrap="none", state="disabled")
        self._log.pack(fill="both")

    # ============================================================
    # 配置读写
    # ============================================================




    def _on_save_config(self) -> None:
        try:
            self._config = self._form.collect()
            self._config.save(self._config_path)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self._parent())
            return
        messagebox.showinfo("成功", "插件配置已保存。", parent=self._parent())

    # ============================================================
    # 文件选择
    # ============================================================
    def _on_import_cookies(self) -> None:
        target = self._config.state_file.strip()
        if not target:
            messagebox.showwarning(
                "提示", "请先在『登录状态』里指定保存路径",
                parent=self._parent(),
            )
            return

        src = filedialog.askopenfilename(
            title="选择浏览器导出的 Cookie JSON",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
            parent=self._parent(),
        )
        if not src:
            return

        try:
            raw = json.loads(Path(src).read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("读取失败", str(e), parent=self._parent())
            return

        try:
            cookies = self._convert_cookies(raw)
        except ValueError as e:
            messagebox.showerror("格式错误", str(e), parent=self._parent())
            return

        state = {"cookies": cookies, "origins": []}
        out = Path(target)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            messagebox.showerror("保存失败", str(e), parent=self._parent())
            return

        self._config.state_file = str(out)
        self._form.load_from(self._config)
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

        messagebox.showinfo(
            "导入成功",
            f"已导入 {len(cookies)} 条 cookie。\n\n保存到：\n{out}",
            parent=self._parent(),
        )

    @staticmethod
    def _convert_cookies(raw) -> list[dict]:
        """接受三种输入：
        1. 已经是 {"cookies": [...], ...} 的 storage_state
        2. EditThisCookie / Cookie-Editor 导出的 list
        3. 只有一个 cookie 的 dict
        返回统一的 Playwright cookie 列表。
        """
        # 已经是 storage_state
        if isinstance(raw, dict) and "cookies" in raw:
            return list(raw.get("cookies", []))

        # 单条 cookie
        if isinstance(raw, dict) and "name" in raw:
            raw = [raw]

        if not isinstance(raw, list):
            raise ValueError("无法识别的 cookie 文件格式（既不是 list 也没有 cookies 字段）")

        SAMESITE_MAP = {
            "no_restriction": "None",
            "none": "None",
            "lax": "Lax",
            "strict": "Strict",
            "unspecified": "Lax",
        }

        cookies: list[dict] = []
        for c in raw:
            name = c.get("name")
            value = c.get("value")
            if not name or value is None:
                continue

            cookie = {
                "name": name,
                "value": str(value),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
                "sameSite": SAMESITE_MAP.get(
                    str(c.get("sameSite", "Lax")).lower(), "Lax",
                ),
            }

            # expirationDate 是 EditThisCookie 的字段，
            # 有些扩展用 expires，两者都兼容
            exp = c.get("expirationDate") or c.get("expires")
            if exp:
                try:
                    cookie["expires"] = int(float(exp))
                except (TypeError, ValueError):
                    pass

            cookies.append(cookie)

        return cookies

    # ============================================================
    # 爬取
    # ============================================================
    def _on_fetch(self) -> None:
        if self._working or self._browser_session is not None:
            return

        # 同步 UI 到配置并保存（这样下次打开路径还在）
        self._config = self._form.collect()
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

        if not self._config.input_excel:
            messagebox.showwarning(
                "提示", "请先选择输入 Excel", parent=self._parent())
            return
        if not Path(self._config.input_excel).exists():
            messagebox.showerror(
                "错误", f"文件不存在：{self._config.input_excel}",
                parent=self._parent())
            return

        self._clear_log()

        try:
            targets = load_targets(self._config.input_excel)
            self._append_log(f"读取到 {len(targets)} 个有效番号")
        except Exception as e:
            messagebox.showerror(
                "读取失败", f"{e}", parent=self._parent())
            return

        if not targets:
            messagebox.showinfo(
                "提示", "Excel 里没有番号", parent=self._parent())
            return

        # 初始化数据模型
        self._rows = [FetchRow(idx=i, target=t) for i, t in enumerate(targets)]
        self._row_by_idx = {r.idx: r for r in self._rows}
        self.result_tree.clear()
        self.result_tree.set_data(self._rows)

        self._stop_event.clear()
        self._working = True
        self._btn_fetch.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._status_var.set(f"爬取中 0/{len(targets)}")

        threading.Thread(
            target=self._fetch_worker,
            args=(targets,), daemon=True,
        ).start()

    def _fetch_worker(self, targets: list[str]) -> None:
        def log(msg: str) -> None:
            self._queue.put(("log", msg))

        def progress(idx0, keyword, payload, status):
            self._queue.put(("progress", (idx0, keyword, payload, status)))

        try:
            records = asyncio.run(scrape_javdb(
                self._config, targets,
                stop_event=self._stop_event,
                on_log=log,
                on_progress=progress,
            ))

            if self._config.output_csv and records:
                try:
                    save_csv(records, self._config.output_csv)
                    log(f"爬取数据已导出: {self._config.output_csv}")
                except Exception as e:
                    log(f"导出 CSV 失败: {e}")

            self._queue.put(("fetch_done", len(records)))
        except Exception as e:
            self._queue.put(("log", f"[错误] 爬取失败: {e}"))
            self._queue.put(("log", traceback.format_exc()))
            self._queue.put(("fetch_done", None))

    def _on_stop(self) -> None:
        if not self._working:
            return
        self._stop_event.set()
        self._append_log("已发送停止信号，等待当前任务结束…")
        self._btn_stop.configure(state="disabled")

    # ============================================================
    # 浏览器会话
    # ============================================================
    def _on_open_browser(self) -> None:
        if self._working or self._browser_session is not None:
            return

        # 保存当前 UI 配置，保证 base_url / state_file 是新的
        self._config = self._form.collect()
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

        if not self._config.state_file:
            if not messagebox.askyesno(
                "提示",
                "尚未配置「登录状态」保存路径。\n\n"
                "现在打开浏览器也可以，但关闭时不会保存登录状态。\n"
                "是否继续？",
                parent=self._parent(),
            ):
                return

        session = BrowserSession(self._config, on_log=self._append_log)
        self._browser_session = session

        # 按钮状态
        self._btn_open_browser.configure(state="disabled")
        self._btn_close_browser.configure(state="disabled")
        self._btn_fetch.configure(state="disabled")
        self._btn_stop.configure(state="disabled")
        self._btn_report.configure(state="disabled")
        self._btn_save.configure(state="disabled")
        self._status_var.set("浏览器启动中…")

        session.start()
        self._schedule(self._poll_browser_ready)

    def _poll_browser_ready(self) -> None:
        session = self._browser_session
        if session is None:
            return

        # 启动失败
        if session.error is not None:
            messagebox.showerror(
                "启动失败",
                f"浏览器启动失败：{session.error}",
                parent=self._parent(),
            )
            self._browser_session = None
            self._restore_buttons_after_browser()
            return

        # 后台线程已结束：可能是启动阶段异常，也可能是浏览器被用户直接关闭
        if session.done:
            self._browser_session = None
            self._restore_buttons_after_browser()
            if session.ready:
                self._append_log(
                    "若未通过「关闭浏览器」按钮操作，本次登录状态不会保存。"
                )
            return

        # 浏览器就绪，把按钮置为可点
        if session.ready:
            self._btn_close_browser.configure(state="normal")
            self._status_var.set("浏览器已启动，请登录")

        # 继续轮询，以便随时发现浏览器被关闭
        self._schedule(self._poll_browser_ready)

    def _on_close_browser(self) -> None:
        session = self._browser_session
        if session is None:
            return
        self._append_log("正在关闭浏览器并保存登录状态…")
        self._btn_close_browser.configure(state="disabled")
        self._status_var.set("正在保存登录状态…")
        session.stop()
        self._schedule(self._poll_browser_closed)

    def _poll_browser_closed(self) -> None:
        session = self._browser_session
        if session is None:
            return
        if session.done:
            self._browser_session = None
            self._restore_buttons_after_browser()
            self._append_log("浏览器已关闭，按钮状态已恢复。")
        else:
            self._schedule(self._poll_browser_closed)

    def _restore_buttons_after_browser(self) -> None:
        self._btn_open_browser.configure(state="normal")
        self._btn_close_browser.configure(state="disabled")
        self._btn_fetch.configure(state="normal")
        self._btn_report.configure(state="normal")
        self._btn_save.configure(state="normal")
        # _btn_stop 保持 disabled（本来就不在爬取中）
        self._btn_stop.configure(state="disabled")
        self._status_var.set("就绪")

    # ============================================================
    # 生成 Excel
    # ============================================================
    def _on_build_excel(self) -> None:
        if self._working or self._browser_session is not None:
            return
        self._config = self._form.collect()

        checks = [
            (self._config.output_csv, "输出 CSV 路径"),
            (self._config.output_excel, "输出 Excel 路径"),
            (self._config.cover_dir, "封面目录"),
        ]
        for value, label in checks:
            if not value:
                messagebox.showwarning(
                    "提示", f"请先填写{label}", parent=self._parent())
                return

        if not Path(self._config.output_csv).exists():
            messagebox.showerror(
                "错误", f"CSV 不存在：{self._config.output_csv}",
                parent=self._parent())
            return

        self._clear_log()
        self._working = True
        self._btn_fetch.configure(state="disabled")
        self._btn_report.configure(state="disabled")
        self._status_var.set("生成 Excel 中…")

        threading.Thread(target=self._excel_worker, daemon=True).start()

    def _excel_worker(self) -> None:
        def log(msg: str) -> None:
            self._queue.put(("log", msg))

        try:
            build_excel(
                self._config.output_csv,
                self._config.output_excel,
                self._config.cover_dir,
                on_log=log,
            )
            self._queue.put(("excel_done", True))
        except Exception as e:
            self._queue.put(("log", f"[错误] 生成 Excel 失败: {e}"))
            self._queue.put(("log", traceback.format_exc()))
            self._queue.put(("excel_done", False))

    # ============================================================
    # 队列消费
    # ============================================================
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self._update_row(*payload)
                elif kind == "fetch_done":
                    self._finish_fetch(payload)
                elif kind == "excel_done":
                    self._finish_excel(payload)
        except queue.Empty:
            pass
        if self._root is not None:
            self._root.after(80, self._poll)

    def _update_row(
        self, idx0: int, keyword: str, payload, status: str,
    ) -> None:
        row = self._row_by_idx.get(idx0)
        if row is None:
            return

        if status == "ok" and isinstance(payload, dict):
            fanhao = payload.get("fanhao", "") or ""
            row.fanhao = fanhao
            row.name = payload.get("name", "") or ""
            row.status = "ok" if is_matched(keyword, fanhao) else "mismatch"
        elif status.startswith("failed"):
            row.status = "failed"
            row.message = status[len("failed:"):].strip()
        elif status == "running":
            row.status = "running"
        elif status == "stopped":
            row.status = "stopped"
        else:
            row.status = status

        self.result_tree.update_row(row)
        self.result_tree.see_row(row)

        total = len(self._rows)
        done = idx0 + 1
        self._status_var.set(f"爬取中 {done}/{total}")

    def _finish_fetch(self, count: Optional[int]) -> None:
        self._working = False
        self._btn_fetch.configure(state="normal")
        self._btn_stop.configure(state="disabled")

        if count is None:
            self._status_var.set("爬取失败")
            summary = "爬取失败"
        else:
            self._status_var.set(f"爬取完成，共 {count} 条")
            summary = f"共抓取 {count} 条"

        # 完成提示（两个开关互相独立）
        if self._config.notify_toast:
            show_toast("JavDB 刮削完成", summary)
        if self._config.notify_sound:
            play_beep()

    def _finish_excel(self, ok: bool) -> None:
        self._working = False
        self._btn_fetch.configure(state="normal")
        self._btn_report.configure(state="normal")
        self._status_var.set("就绪" if ok else "生成失败")
        if ok:
            messagebox.showinfo(
                "完成",
                f"Excel 已生成：\n{self._config.output_excel}",
                parent=self._parent(),
            )

    # ---------- 日志 ----------
    def _append_log(self, msg: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _parent(self) -> tk.Misc:
        """messagebox 的 parent。优先用插件标签页，未创建时退回主窗口。"""
        return self._root if self._root is not None else self.ctx.app

    def _schedule(self, fn, delay_ms: int = 200) -> None:
        """安全地排一个延迟任务到 tkinter 主循环。"""
        if self._root is not None:
            self._root.after(delay_ms, fn)
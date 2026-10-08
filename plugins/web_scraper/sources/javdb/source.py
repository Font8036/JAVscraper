"""JavDB 源：实现 Source 协议。"""

from __future__ import annotations

import json
import asyncio
import queue
import threading
import tkinter as tk
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

from av_scraper import fileio
from av_scraper.gui.config_window import ConfigPage
from av_scraper.widgets import (
    Column,
    ConfigForm,
    SortableTreeview,
    resolve_locked_target,
)

from ...shared.browser import BrowserSession
from ...shared.notify import play_beep, show_toast
from .config import JavdbSourceConfig, normalize_base_url
from .fetch import load_targets, save_csv, scrape_javdb
from .parse import is_matched
from .report import build_excel


@dataclass
class FetchRow:
    idx: int
    target: str
    fanhao: str = ""
    name: str = ""
    status: str = "waiting"    # waiting/running/ok/mismatch/failed/stopped
    message: str = ""


class JavdbSource:
    name = "JavDB 信息"

    def __init__(self, plugin: Any, ctx: Any) -> None:
        self.plugin = plugin
        self.ctx = ctx
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._working = False
        self._root: Optional[ttk.Frame] = None

        self._result_tree: Optional[SortableTreeview] = None
        self._log: Optional[tk.Text] = None
        self._status_var: Optional[tk.StringVar] = None

        self._btn_open_browser: Optional[ttk.Button] = None
        self._btn_close_browser: Optional[ttk.Button] = None
        self._btn_fetch: Optional[ttk.Button] = None
        self._btn_stop: Optional[ttk.Button] = None
        self._btn_report: Optional[ttk.Button] = None

        self._browser_session: Optional[BrowserSession] = None
        self._rows: list[FetchRow] = []
        self._row_by_idx: dict[int, FetchRow] = {}

    # ---------- 配置访问 ----------
    @property
    def _config(self) -> JavdbSourceConfig:
        return self.plugin.config.javdb

    @_config.setter
    def _config(self, value: JavdbSourceConfig) -> None:
        self.plugin.config.javdb = value

    @property
    def _config_path(self) -> Path:
        return self.plugin.config_path

    def _parent(self) -> tk.Misc:
        return self._root if self._root is not None else self.ctx.app

    # ============================================================
    # Source 协议
    # ============================================================
    def build_tab(self, parent) -> ttk.Frame:
        root = ttk.Frame(parent, padding=8)
        self._root = root
        self._polling = False        # 挡住处置弹窗（嵌套事件循环）期间的重入
        self._build(root)
        root.after(80, self._poll)
        return root

    def is_busy(self) -> bool:
        return self._working or self._browser_session is not None

    def apply_layout(self) -> None:
        cfg = self.ctx.app_config
        if self._result_tree is not None:
            self._result_tree.tree.configure(height=cfg.table_height)
        if self._log is not None:
            self._log.configure(height=cfg.log_height)

    def sync_from_config(self) -> None:
        # 配置只在配置窗口里改，主界面无需同步
        pass

    def config_page(self) -> ConfigPage:
        forms: dict[str, ConfigForm] = {}

        def build(parent):
            frame = ttk.Frame(parent)
            form = ConfigForm(frame, JavdbSourceConfig)
            form.pack(fill="both", expand=True)
            forms["main"] = form

            # 把「导入浏览器 Cookie」按钮塞到"登录状态"那一行
            state_row = form.get_row_frame("state_file")
            if state_row is not None:
                ttk.Button(
                    state_row, text="导入浏览器 Cookie",
                    command=self._on_import_cookies,
                ).pack(side="left", padx=(4, 0))
            return frame

        def load(cfg):
            forms["main"].load_from(cfg)

        def collect():
            return forms["main"].collect()

        def save(cfg: JavdbSourceConfig):
            self._config = cfg
            self.plugin.save_config()

        def current():
            return self._config

        def default():
            return JavdbSourceConfig()

        return ConfigPage(
            title=self.name,
            build=build, load=load, collect=collect,
            save=save, current=current, default=default,
        )

    # ============================================================
    # UI 构建
    # ============================================================
    def _build(self, root: ttk.Frame) -> None:
        hint = ttk.Frame(root)
        hint.pack(fill="x", pady=(0, 4))
        ttk.Label(
            hint,
            text="⚙ 所有配置项已移至右下角「配置」窗口。",
            foreground="#888",
        ).pack(side="left")

        actions = ttk.Frame(root, padding=(0, 4))
        actions.pack(fill="x")

        self._btn_open_browser = ttk.Button(
            actions, text="启动浏览器", command=self._on_open_browser)
        self._btn_open_browser.pack(side="left")
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

        def _status_text(r: FetchRow) -> str:
            return {
                "waiting": "等待",
                "running": "进行中…",
                "ok": "✓ 完成",
                "mismatch": "⚠ 完成（番号不同）",
                "failed": "✗ 失败" + (f"：{r.message[:30]}" if r.message else ""),
                "stopped": "○ 已停止",
            }.get(r.status, r.status)

        def _status_rank(r: FetchRow) -> int:
            return {
                "failed": 0, "mismatch": 1, "stopped": 2,
                "running": 3, "waiting": 4, "ok": 5,
            }.get(r.status, 9)

        def _tag(r: FetchRow) -> tuple:
            return {
                "ok": ("ok",), "mismatch": ("warn",),
                "failed": ("failed",), "running": ("running",),
                "stopped": ("stopped",),
            }.get(r.status, ())

        self._result_tree = SortableTreeview(
            root,
            key=lambda r: f"row_{r.idx}",
            height=self.ctx.app_config.table_height,
            searchable=True,
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
            row_tags=_tag,
            tag_configure={
                "ok": {"foreground": "#1a7f37"},
                "running": {"foreground": "#0a58ca"},
                "warn": {"foreground": "#c77b00"},
                "failed": {"foreground": "#c0392b"},
                "stopped": {"foreground": "#999999"},
            },
        )
        self._result_tree.pack(fill="both", expand=True, pady=(8, 0))

        ttk.Label(root, text="日志:").pack(anchor="w", pady=(6, 0))
        self._log = tk.Text(root, height=self.ctx.app_config.log_height, wrap="none", state="disabled")
        self._log.pack(fill="both")

    def _schedule(self, fn, delay_ms: int = 200) -> None:
        if self._root is not None:
            self._root.after(delay_ms, fn)

    # ============================================================
    # 浏览器会话
    # ============================================================
    def _on_open_browser(self) -> None:
        if self._working or self._browser_session is not None:
            return

        if not self._config.state_file:
            if not messagebox.askyesno(
                "提示",
                "尚未配置「登录状态」保存路径。\n\n"
                "继续打开浏览器不会自动保存登录状态。是否继续？",
                parent=self._parent(),
            ):
                return

        session = BrowserSession(
            base_url=self._config.base_url,
            channel=self._config.browser_channel,
            state_file=self._config.state_file,
            on_log=self._append_log,
        )
        self._browser_session = session

        if self._btn_open_browser: self._btn_open_browser.configure(state="disabled")
        if self._btn_close_browser: self._btn_close_browser.configure(state="disabled")
        if self._btn_fetch: self._btn_fetch.configure(state="disabled")
        if self._btn_stop: self._btn_stop.configure(state="disabled")
        if self._btn_report: self._btn_report.configure(state="disabled")
        if self._status_var: self._status_var.set("浏览器启动中…")

        session.start()
        self._schedule(self._poll_browser_ready)

    def _poll_browser_ready(self) -> None:
        session = self._browser_session
        if session is None:
            return
        if session.error is not None:
            messagebox.showerror(
                "启动失败", f"浏览器启动失败：{session.error}",
                parent=self._parent())
            self._browser_session = None
            self._restore_buttons_after_browser()
            return
        if session.done:
            self._browser_session = None
            self._restore_buttons_after_browser()
            return
        if session.ready:
            if self._btn_close_browser:
                self._btn_close_browser.configure(state="normal")
            if self._status_var:
                self._status_var.set("浏览器已启动，请登录")
        self._schedule(self._poll_browser_ready)

    def _on_close_browser(self) -> None:
        session = self._browser_session
        if session is None:
            return
        self._append_log("正在关闭浏览器并保存登录状态…")
        if self._btn_close_browser:
            self._btn_close_browser.configure(state="disabled")
        if self._status_var:
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
        if self._btn_open_browser: self._btn_open_browser.configure(state="normal")
        if self._btn_close_browser: self._btn_close_browser.configure(state="disabled")
        if self._btn_fetch: self._btn_fetch.configure(state="normal")
        if self._btn_report: self._btn_report.configure(state="normal")
        if self._btn_stop: self._btn_stop.configure(state="disabled")
        if self._status_var: self._status_var.set("就绪")

    # ============================================================
    # 导入 Cookie
    # ============================================================
    def _on_import_cookies(self) -> None:
        target = self._config.state_file.strip()
        if not target:
            messagebox.showwarning(
                "提示", "请先在『登录状态』里指定保存路径",
                parent=self._parent())
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
            cookies = self._convert_cookies(raw)
        except Exception as e:
            messagebox.showerror("导入失败", str(e), parent=self._parent())
            return

        state = {"cookies": cookies, "origins": []}
        out = Path(target)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            messagebox.showerror("保存失败", str(e), parent=self._parent())
            return

        self._config.state_file = str(out)
        self.plugin.save_config()

        messagebox.showinfo(
            "导入成功",
            f"已导入 {len(cookies)} 条 cookie。\n\n保存到：\n{out}",
            parent=self._parent())

    @staticmethod
    def _convert_cookies(raw) -> list[dict]:
        if isinstance(raw, dict) and "cookies" in raw:
            return list(raw.get("cookies", []))
        if isinstance(raw, dict) and "name" in raw:
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError("无法识别的 cookie 文件格式")
        SAMESITE_MAP = {
            "no_restriction": "None", "none": "None",
            "lax": "Lax", "strict": "Strict", "unspecified": "Lax",
        }
        cookies = []
        for c in raw:
            name = c.get("name")
            value = c.get("value")
            if not name or value is None:
                continue
            cookie = {
                "name": name, "value": str(value),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
                "sameSite": SAMESITE_MAP.get(
                    str(c.get("sameSite", "Lax")).lower(), "Lax"),
            }
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
            messagebox.showerror("读取失败", f"{e}", parent=self._parent())
            return

        if not targets:
            messagebox.showinfo(
                "提示", "Excel 里没有番号", parent=self._parent())
            return

        self._rows = [FetchRow(idx=i, target=t) for i, t in enumerate(targets)]
        self._row_by_idx = {r.idx: r for r in self._rows}
        if self._result_tree:
            self._result_tree.clear()
            self._result_tree.set_data(self._rows)

        self._stop_event.clear()
        self._working = True
        if self._btn_fetch: self._btn_fetch.configure(state="disabled")
        if self._btn_stop: self._btn_stop.configure(state="normal")
        if self._status_var:
            self._status_var.set(f"爬取中 0/{len(targets)}")

        threading.Thread(
            target=self._fetch_worker, args=(targets,), daemon=True,
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
        if self._btn_stop:
            self._btn_stop.configure(state="disabled")

    # ============================================================
    # 生成 Excel
    # ============================================================
    def _on_build_excel(self) -> None:
        if self._working or self._browser_session is not None:
            return

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
        if self._btn_fetch: self._btn_fetch.configure(state="disabled")
        if self._btn_report: self._btn_report.configure(state="disabled")
        if self._status_var: self._status_var.set("生成 Excel 中…")

        threading.Thread(target=self._excel_worker, daemon=True).start()

    def _excel_worker(self) -> None:
        def log(msg: str) -> None:
            self._queue.put(("log", msg))

        # 先写到临时文件（慢活留在后台线程），最后只把"发布"这一步交给主线程：
        # 目标被占用时重试只需要重做发布，不必重新生成整张表
        target = Path(self._config.output_excel)
        temp = fileio.temp_path_for(target)
        try:
            build_excel(
                self._config.output_csv,
                temp,
                self._config.cover_dir,
                on_log=log,
            )
        except Exception as e:
            self._queue.put(("log", f"[错误] 生成 Excel 失败: {e}"))
            self._queue.put(("log", traceback.format_exc()))
            fileio.discard(temp)
            self._queue.put(("excel_done", None))
            return

        try:
            final = fileio.publish(temp, target)
        except OSError as e:
            # 这里不能弹窗（在 worker 线程），交给主线程处置
            self._queue.put(("save_failed", (temp, target, e)))
            return

        self._queue.put(("log", f"[提示] 报告已保存到：{final}"))
        self._queue.put(("excel_done", str(final)))

    # ============================================================
    # 队列消费
    # ============================================================
    def _poll(self) -> None:
        # 处置弹窗（save_guard）会开启嵌套事件循环，after 回调会在弹窗期间再次进来；
        # 这里挡住重入，避免把后面的消息也处理掉（弹窗叠弹窗、状态还超前）
        if self._polling:
            return
        self._polling = True
        try:
            while True:
                try:
                    kind, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self._update_row(*payload)
                elif kind == "fetch_done":
                    self._finish_fetch(payload)
                elif kind == "excel_done":
                    self._finish_excel(payload)
                elif kind == "save_failed":
                    self._resolve_save_failure(*payload)
        finally:
            # 用 finally 保证即使某个处理函数抛错，轮询也不会永久停掉
            self._polling = False
            if self._root is not None:
                self._root.after(80, self._poll)

    def _update_row(self, idx0, keyword, payload, status) -> None:
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

        if self._result_tree:
            self._result_tree.update_row(row)
            self._result_tree.see_row(row)

        if self._status_var:
            total = len(self._rows)
            self._status_var.set(f"爬取中 {idx0 + 1}/{total}")

    def _finish_fetch(self, count: Optional[int]) -> None:
        self._working = False
        if self._btn_fetch: self._btn_fetch.configure(state="normal")
        if self._btn_stop: self._btn_stop.configure(state="disabled")
        if count is None:
            if self._status_var: self._status_var.set("爬取失败")
            summary = "爬取失败"
        else:
            if self._status_var: self._status_var.set(f"爬取完成，共 {count} 条")
            summary = f"共抓取 {count} 条"
        if self._config.notify_toast:
            show_toast("JavDB 刮削完成", summary)
        if self._config.notify_sound:
            play_beep()

    def _finish_excel(self, saved_to: Optional[str]) -> None:
        """saved_to：报告实际保存到的路径；None 表示没保存成功。"""
        self._working = False
        if self._btn_fetch: self._btn_fetch.configure(state="normal")
        if self._btn_report: self._btn_report.configure(state="normal")
        if self._status_var:
            self._status_var.set("就绪" if saved_to else "生成失败")
        if saved_to:
            messagebox.showinfo(
                "完成",
                f"Excel 已生成：\n{saved_to}",
                parent=self._parent())

    def _resolve_save_failure(self, temp: Path, target: Path, exc: OSError) -> None:
        """主线程：目标文件被占用（通常是用户还开着这张表），按用户选择处置。

        注意这里重试的只是"把已经生成好的临时文件换过去"，不需要重新生成表格。
        """
        self._append_log(f"[提示] 无法写入 {target}：{exc}")
        final = resolve_locked_target(self._parent(), temp, target)

        if final is None:
            # 用户关掉了处置窗口：内容不丢，留在临时文件里并告诉他路径
            self._append_log(f"[提示] 报告已生成在：{temp}")
            self._append_log("[提示] 关掉占用它的程序后，把上面这个文件改名即可使用。")
            self._finish_excel(None)
            if self._status_var:
                self._status_var.set("报告未保存")
            return

        self._append_log(f"[提示] 报告已保存到：{final}")
        self._finish_excel(str(final))

    # ---------- 日志 ----------
    def _append_log(self, msg: str) -> None:
        if self._log is None:
            return
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self) -> None:
        if self._log is None:
            return
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
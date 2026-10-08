"""字幕猫源：实现 Source 协议。"""

from __future__ import annotations

import asyncio
import json
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
from av_scraper.scraper import CodeExtractor
from av_scraper.widgets import (
    Column,
    ConfigForm,
    SortableTreeview,
    resolve_locked_target,
)

from ...shared.browser import BrowserSession
from ...shared.notify import play_beep, show_toast
from .config import SubtitleCatConfig
from .fetch import load_targets, scrape_subtitlecat
from .report import build_excel


@dataclass
class SubtitleRow:
    idx: int
    target: str
    search_count: int = 0
    actual_code: str = ""
    title: str = ""
    source_lang: str = ""
    downloads: int = 0
    status: str = "等待"


class SubtitleCatSource:
    name = "字幕猫"

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

        self._browser_session: Optional[BrowserSession] = None
        self._rows: list[SubtitleRow] = []
        self._row_by_idx: dict[int, SubtitleRow] = {}

        # 复用核心的 CodeExtractor
        try:
            self._extractor = CodeExtractor(ctx.app_config.scraper)
        except Exception:
            from av_scraper.config import ScraperConfig
            self._extractor = CodeExtractor(ScraperConfig())

    # ---------- 配置访问 ----------
    @property
    def _config(self) -> SubtitleCatConfig:
        return self.plugin.config.subtitlecat

    @_config.setter
    def _config(self, value: SubtitleCatConfig) -> None:
        self.plugin.config.subtitlecat = value

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
        pass

    def config_page(self) -> ConfigPage:
        forms: dict[str, ConfigForm] = {}

        def build(parent):
            frame = ttk.Frame(parent)
            form = ConfigForm(frame, SubtitleCatConfig)
            form.pack(fill="both", expand=True)
            forms["main"] = form

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

        def save(cfg: SubtitleCatConfig):
            self._config = cfg

        def current():
            return self._config

        def default():
            return SubtitleCatConfig()

        return ConfigPage(
            title=self.name,
            build=build, load=load, collect=collect,
            save=save, current=current, default=default,
        )

    # ============================================================
    # UI
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

        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(actions, textvariable=self._status_var).pack(
            side="left", padx=12)

        def _status_text(r: SubtitleRow) -> str:
            return r.status

        def _status_rank(r: SubtitleRow) -> int:
            s = r.status
            if "下载失败" in s or s == "未搜到":
                return 0
            if "番号不一致" in s:
                return 1
            if s == "等待翻译":
                return 2
            if s == "下载成功":
                return 3
            return 4

        def _tag(r: SubtitleRow) -> tuple:
            s = r.status
            if "下载失败" in s or s == "未搜到" or s == "已停止":
                return ("failed",)
            if "番号不一致" in s:
                return ("mismatch",)
            return ()

        self._result_tree = SortableTreeview(
            root,
            key=lambda r: f"row_{r.idx}",
            height=self.ctx.app_config.table_height,
            searchable=True,
            columns=[
                Column("target", "目标番号", 110,
                       display=lambda r: r.target,
                       sort=lambda r: r.target.lower()),
                Column("count", "搜索结果数量", 120,
                       display=lambda r: str(r.search_count),
                       sort=lambda r: r.search_count,
                       anchor="center"),
                Column("code", "实际番号", 110,
                       display=lambda r: r.actual_code or "—",
                       sort=lambda r: (r.actual_code or "").lower()),
                Column("title", "名称", 380,
                       display=lambda r: r.title or "—",
                       sort=lambda r: (r.title or "").lower(),
                       stretch=True),
                Column("lang", "源语言", 80,
                       display=lambda r: r.source_lang or "—",
                       sort=lambda r: r.source_lang),
                Column("dl", "下载量", 70,
                       display=lambda r: str(r.downloads),
                       sort=lambda r: r.downloads,
                       anchor="center"),
                Column("status", "状态", 180,
                       display=_status_text,
                       sort=_status_rank),
            ],
            row_tags=_tag,
            tag_configure={
                "failed": {"foreground": "#c0392b"},
                "mismatch": {"foreground": "#c77b00"},
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

        for btn in (self._btn_open_browser, self._btn_close_browser,
                    self._btn_fetch, self._btn_stop):
            if btn:
                btn.configure(state="disabled")
        if self._status_var:
            self._status_var.set("浏览器启动中…")

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
        for btn in (self._btn_open_browser, self._btn_fetch):
            if btn:
                btn.configure(state="normal")
        if self._btn_close_browser:
            self._btn_close_browser.configure(state="disabled")
        if self._btn_stop:
            self._btn_stop.configure(state="disabled")
        if self._status_var:
            self._status_var.set("就绪")

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
            cookies = _convert_cookies(raw)
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

        messagebox.showinfo(
            "导入成功",
            f"已导入 {len(cookies)} 条 cookie。\n\n保存到：\n{out}",
            parent=self._parent())

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
        if not self._config.target_dir:
            messagebox.showwarning(
                "提示", "请先选择目标目录", parent=self._parent())
            return
        if not self._config.output_excel:
            messagebox.showwarning(
                "提示", "请先指定输出 Excel", parent=self._parent())
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

        self._rows = [SubtitleRow(idx=i, target=t)
                      for i, t in enumerate(targets)]
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

        def progress(idx, keyword, record, status):
            self._queue.put(("progress", (idx, record, status)))

        try:
            records = asyncio.run(scrape_subtitlecat(
                self._config, targets, self._extractor,
                stop_event=self._stop_event,
                on_log=log,
                on_progress=progress,
            ))

            # 生成 Excel：先写临时文件（慢活留在后台线程），发布这一步若遇到
            # "文件被占用"，交给主线程弹窗处置 —— 不需要重新爬一遍
            target = Path(self._config.output_excel)
            temp = fileio.temp_path_for(target)
            saved_to: Optional[str] = ""
            try:
                build_excel(records, temp, on_log=log)
            except Exception as e:
                log(f"[错误] 生成报告失败：{e}")
                log(traceback.format_exc())
                fileio.discard(temp)
            else:
                try:
                    saved_to = str(fileio.publish(temp, target))
                    log(f"已生成报告：{saved_to}")
                except OSError as e:
                    saved_to = None            # 待用户处置
                    self._queue.put(("save_failed", (temp, target, e)))

            self._queue.put(
                ("fetch_done", (len(records), saved_to)))
        except Exception as e:
            self._queue.put(("log", f"[错误] 爬取失败：{e}"))
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
    # 队列消费
    # ============================================================
    def _poll(self) -> None:
        # 处置弹窗（save_guard）会开启嵌套事件循环，after 回调会在弹窗期间再次进来；
        # 这里挡住重入，避免总结弹窗叠在处置弹窗上、还抢先说出用户没决定的状态
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
                elif kind == "save_failed":
                    self._resolve_save_failure(*payload)
        finally:
            # 用 finally 保证即使某个处理函数抛错，轮询也不会永久停掉
            self._polling = False
            if self._root is not None:
                self._root.after(80, self._poll)

    def _update_row(self, idx: int, record: dict, status: str) -> None:
        row = self._row_by_idx.get(idx)
        if row is None:
            return

        row.search_count = int(record.get("search_count", 0))
        row.actual_code = record.get("actual_code", "") or ""
        row.title = record.get("title", "") or ""
        row.source_lang = record.get("source_lang", "") or ""
        row.downloads = int(record.get("downloads", 0))
        row.status = status or "等待"

        if self._result_tree:
            self._result_tree.update_row(row)
            self._result_tree.see_row(row)

        if self._status_var:
            total = len(self._rows)
            self._status_var.set(f"爬取中 {idx + 1}/{total}")

    def _finish_fetch(self, payload) -> None:
        self._working = False
        if self._btn_fetch: self._btn_fetch.configure(state="normal")
        if self._btn_stop: self._btn_stop.configure(state="disabled")

        # 爬取整体失败
        if payload is None:
            if self._status_var:
                self._status_var.set("爬取失败")
            if self._config.notify_sound:
                play_beep()
            messagebox.showerror(
                "爬取失败",
                "爬取过程中出现错误，详情见日志。",
                parent=self._parent())
            return

        count, saved_to = payload
        summary = f"共处理 {count} 条"
        if self._status_var:
            self._status_var.set(f"爬取完成，共 {count} 条")

        # 完成提示（声音 / 系统通知）
        if self._config.notify_toast:
            show_toast("字幕猫爬取完成", summary)
        if self._config.notify_sound:
            play_beep()

        if saved_to:
            messagebox.showinfo(
                "完成",
                f"{summary}\n\n报告已保存到：\n{saved_to}",
                parent=self._parent())
        elif saved_to == "":
            messagebox.showwarning(
                "完成（报告生成失败）",
                f"{summary}\n\n"
                f"但报告没能生成，详情见日志。\n\n"
                f"目标路径：\n{self._config.output_excel}",
                parent=self._parent())
        else:
            # None：目标被占用，处置弹窗已经问过用户了，这里只做收尾说明
            messagebox.showinfo(
                "完成",
                f"{summary}\n\n报告没有保存到目标路径，"
                "已生成的内容和位置见下方日志。",
                parent=self._parent())

    def _resolve_save_failure(self, temp: Path, target: Path, exc: OSError) -> None:
        """主线程：报告目标被占用时按用户选择处置（重试只重做"发布"这一步）。"""
        self._append_log(f"[提示] 无法写入 {target}：{exc}")
        final = resolve_locked_target(self._parent(), temp, target)

        if final is None:
            self._append_log(f"[提示] 报告已生成在：{temp}")
            self._append_log("[提示] 关掉占用它的程序后，把上面这个文件改名即可使用。")
            return

        self._append_log(f"[提示] 报告已保存到：{final}")

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


# ============================================================
# Cookie 转换（独立函数，和 javdb 的共用逻辑一致）
# ============================================================
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
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

from av_scraper.plugin_api import PluginContext

from .config import JavdbConfig
from .fetch import load_targets, save_csv, scrape_javdb
from .report import build_excel


class JavdbPlugin:
    name = "JavDB 刮削"
    version = "1.0.0"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self._config_path = self._resolve_config_path()
        self._config = JavdbConfig.load(self._config_path)
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._working = False
        self._root: Optional[ttk.Frame] = None

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
        self._load_to_ui()
        root.after(80, self._poll)
        return root

    # ============================================================
    # UI 构建
    # ============================================================
    def _build(self, root: ttk.Frame) -> None:
        # -------- 路径区 --------
        paths = ttk.LabelFrame(root, text="路径", padding=8)
        paths.pack(fill="x", pady=(0, 6))

        self._var_input = self._make_path_row(
            paths, "输入 Excel:", "file")
        self._var_cover = self._make_path_row(
            paths, "封面目录:", "dir")
        self._var_csv = self._make_path_row(
            paths, "输出 CSV:", "save", ".csv")
        self._var_excel = self._make_path_row(
            paths, "输出 Excel:", "save", ".xlsx")
        # 登录状态行（多一个"导入"按钮）
        state_row = ttk.Frame(paths); state_row.pack(fill="x", pady=2)
        ttk.Label(state_row, text="登录状态:", width=14, anchor="e").pack(side="left")
        self._var_state = tk.StringVar()
        ttk.Entry(state_row, textvariable=self._var_state).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(
            state_row, text="浏览…",
            command=lambda: self._pick_path(self._var_state, "save", ".json"),
        ).pack(side="left")
        ttk.Button(
            state_row, text="导入浏览器 Cookie",
            command=self._on_import_cookies,
        ).pack(side="left", padx=(4, 0))

        # -------- 参数区 --------
        opts = ttk.LabelFrame(root, text="参数", padding=8)
        opts.pack(fill="x", pady=(0, 6))

        row1 = ttk.Frame(opts); row1.pack(fill="x", pady=2)
        self._var_show_browser = tk.BooleanVar()
        ttk.Checkbutton(
            row1, text="显示浏览器窗口", variable=self._var_show_browser,
        ).pack(side="left", padx=(0, 16))
        ttk.Label(row1, text="浏览器:").pack(side="left")
        self._var_channel = tk.StringVar()
        ttk.Combobox(
            row1, textvariable=self._var_channel,
            values=["msedge", "chrome", "chromium"],
            state="readonly", width=12,
        ).pack(side="left", padx=4)

        row2 = ttk.Frame(opts); row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="请求间隔(秒):").pack(side="left")
        self._var_delay = tk.StringVar()
        ttk.Spinbox(
            row2, from_=0.0, to=10.0, increment=0.5,
            textvariable=self._var_delay, width=6,
        ).pack(side="left", padx=(4, 16))
        ttk.Label(row2, text="超时(秒):").pack(side="left")
        self._var_timeout = tk.StringVar()
        ttk.Spinbox(
            row2, from_=5, to=300, increment=5,
            textvariable=self._var_timeout, width=6,
        ).pack(side="left", padx=(4, 16))
        ttk.Label(row2, text="重试次数:").pack(side="left")
        self._var_retry = tk.StringVar()
        ttk.Spinbox(
            row2, from_=0, to=5,
            textvariable=self._var_retry, width=4,
        ).pack(side="left", padx=4)

        row3 = ttk.Frame(opts); row3.pack(fill="x", pady=2)
        self._var_skip_covers = tk.BooleanVar()
        ttk.Checkbutton(
            row3, text="跳过已存在的封面", variable=self._var_skip_covers,
        ).pack(side="left", padx=(0, 16))
        self._var_save_state = tk.BooleanVar()
        ttk.Checkbutton(
            row3, text="退出时保存登录状态", variable=self._var_save_state,
        ).pack(side="left")

        # -------- 按钮区 --------
        actions = ttk.Frame(root, padding=(0, 4))
        actions.pack(fill="x")
        ttk.Button(
            actions, text="保存配置", command=self._on_save_config,
        ).pack(side="left")
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
        cols = ("fanhao", "name", "status")
        self._tree = ttk.Treeview(
            root, columns=cols, show="headings", height=10)
        for c, t, w in zip(
            cols, ("番号", "名称", "状态"), (140, 500, 160),
        ):
            self._tree.heading(c, text=t)
            self._tree.column(c, width=w, anchor="w",
                              stretch=(c == "name"))
        self._tree.tag_configure("ok", foreground="#1a7f37")
        self._tree.tag_configure("running", foreground="#0a58ca")
        self._tree.tag_configure("failed", foreground="#c0392b")
        self._tree.tag_configure("stopped", foreground="#999999")
        self._tree.pack(fill="both", expand=True, pady=(8, 0))

        # -------- 日志 --------
        ttk.Label(root, text="日志:").pack(anchor="w", pady=(6, 0))
        self._log = tk.Text(root, height=8, wrap="none", state="disabled")
        self._log.pack(fill="both")

    def _make_path_row(
        self, parent, label: str, kind: str, ext: Optional[str] = None,
    ) -> tk.StringVar:
        row = ttk.Frame(parent); row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=14, anchor="e").pack(side="left")
        var = tk.StringVar()
        ttk.Entry(row, textvariable=var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(
            row, text="浏览…",
            command=lambda v=var, k=kind, e=ext: self._pick_path(v, k, e),
        ).pack(side="left")
        return var

    # ============================================================
    # 配置读写
    # ============================================================
    def _load_to_ui(self) -> None:
        cfg = self._config
        self._var_input.set(cfg.input_excel)
        self._var_cover.set(cfg.cover_dir)
        self._var_csv.set(cfg.output_csv)
        self._var_excel.set(cfg.output_excel)
        self._var_state.set(cfg.state_file)

        self._var_show_browser.set(cfg.show_browser)
        self._var_channel.set(cfg.browser_channel)
        self._var_delay.set(str(cfg.request_delay))
        self._var_timeout.set(str(max(1, cfg.page_timeout // 1000)))
        self._var_retry.set(str(cfg.max_retries))
        self._var_skip_covers.set(cfg.skip_existing_covers)
        self._var_save_state.set(cfg.save_state_on_exit)

    def _ui_to_config(self) -> JavdbConfig:
        def _f(s, default):
            try:
                return float(s)
            except (TypeError, ValueError):
                return default

        def _i(s, default):
            try:
                return int(s)
            except (TypeError, ValueError):
                return default

        return JavdbConfig(
            input_excel=self._var_input.get().strip(),
            cover_dir=self._var_cover.get().strip(),
            output_csv=self._var_csv.get().strip(),
            output_excel=self._var_excel.get().strip(),
            state_file=self._var_state.get().strip(),
            show_browser=bool(self._var_show_browser.get()),
            browser_channel=self._var_channel.get().strip() or "msedge",
            request_delay=_f(self._var_delay.get(), 1.0),
            page_timeout=int(_f(self._var_timeout.get(), 60) * 1000),
            max_retries=_i(self._var_retry.get(), 2),
            skip_existing_covers=bool(self._var_skip_covers.get()),
            save_state_on_exit=bool(self._var_save_state.get()),
        )

    def _on_save_config(self) -> None:
        try:
            self._config = self._ui_to_config()
            self._config.save(self._config_path)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self._parent())
            return
        messagebox.showinfo("成功", "插件配置已保存。", parent=self._parent())

    # ============================================================
    # 文件选择
    # ============================================================
    def _pick_path(
        self, var: tk.StringVar, kind: str, ext: Optional[str] = None,
    ) -> None:
        current = var.get().strip()
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=current or None)
        elif kind == "file":
            init = str(Path(current).parent) if current else None
            p = filedialog.askopenfilename(
                initialdir=init,
                filetypes=[
                    ("Excel / CSV / JSON", "*.xlsx *.xls *.csv *.json"),
                    ("所有文件", "*.*"),
                ],
            )
        elif kind == "save":
            init = str(Path(current).parent) if current else None
            kwargs: dict[str, Any] = {"initialdir": init}
            if ext:
                kwargs["defaultextension"] = ext
            p = filedialog.asksaveasfilename(**kwargs)
        else:
            return
        if p:
            var.set(p)

    def _on_import_cookies(self) -> None:
        """从浏览器扩展导出的 cookie JSON 导入，转成 Playwright storage_state。"""
        target = self._var_state.get().strip()
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

        # 顺手把路径写回 UI 和插件配置
        self._var_state.set(str(out))
        try:
            self._config = self._ui_to_config()
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
        if self._working:
            return

        # 同步 UI 到配置并保存（这样下次打开路径还在）
        self._config = self._ui_to_config()
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

        self._tree.delete(*self._tree.get_children())
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

        for i, t in enumerate(targets):
            self._tree.insert(
                "", "end", iid=f"row_{i}",
                values=(t, "—", "等待"), tags=("",),
            )

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

        def progress(idx0, keyword, name, status):
            self._queue.put(("progress", (idx0, keyword, name, status)))

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
    # 生成 Excel
    # ============================================================
    def _on_build_excel(self) -> None:
        if self._working:
            return
        self._config = self._ui_to_config()

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
        self, idx0: int, keyword: str, name: str, status: str,
    ) -> None:
        iid = f"row_{idx0}"
        if not self._tree.exists(iid):
            return

        current = self._tree.item(iid, "values")
        display_name = name or (current[1] if len(current) > 1 else "—")
        if display_name == "":
            display_name = "—"

        if status.startswith("failed"):
            err = status[len("failed:"):].strip()
            text = f"✗ 失败" + (f"：{err[:30]}" if err else "")
            tag = "failed"
        elif status == "running":
            text, tag = "进行中…", "running"
        elif status == "ok":
            text, tag = "✓ 完成", "ok"
        elif status == "stopped":
            text, tag = "○ 已停止", "stopped"
        else:
            text, tag = status, ""

        self._tree.item(iid, values=(keyword, display_name, text), tags=(tag,))
        self._tree.see(iid)

        try:
            total = len(self._tree.get_children())
            done = idx0 + 1
            self._status_var.set(f"爬取中 {done}/{total}")
        except Exception:
            pass

    def _finish_fetch(self, count: Optional[int]) -> None:
        self._working = False
        self._btn_fetch.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        if count is None:
            self._status_var.set("爬取失败")
        else:
            self._status_var.set(f"爬取完成，共 {count} 条")

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
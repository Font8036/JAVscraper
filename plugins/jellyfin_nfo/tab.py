"""Jellyfin NFO 插件的 GUI。"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

from av_scraper.plugin_api import PluginContext
from av_scraper.widgets import Column, ConfigForm, SortableTreeview

from .config import JellyfinNfoConfig
from .excel_reader import read_excel_data
from .image_extractor import extract_covers_from_excel
from .organizer import organize_movie_folder

logger = logging.getLogger(__name__)


class JellyfinNfoPlugin:
    name = "Jellyfin NFO"
    version = "1.3"

    _form: ConfigForm       # ← 加这一行，告诉 Pylance 所有实例都有此属性

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self._config_path = self._resolve_config_path()
        self._config = JellyfinNfoConfig.load(self._config_path)
        self._queue: queue.Queue = queue.Queue()
        self._working = False
        self._root: Optional[ttk.Frame] = None

    def _resolve_config_path(self) -> Path:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and str(self.ctx.plugin_dir).startswith(str(meipass)):
            return Path(sys.executable).resolve().parent / "jellyfin_nfo_config.json"
        return self.ctx.plugin_dir / "config.json"

    def _parent(self) -> tk.Misc:
        return self._root if self._root is not None else self.ctx.app

    # ============================================================
    # 插件协议
    # ============================================================
    def create_tab(self, notebook) -> ttk.Frame:
        root = ttk.Frame(notebook, padding=8)
        self._root = root
        self._build(root)
        self._form.load_from(self._config)
        root.after(80, self._poll)
        return root

    # ============================================================
    # UI
    # ============================================================
    def _build(self, root: ttk.Frame) -> None:
        # ---- 配置区 ----
        cfg_frame = ttk.LabelFrame(root, text="配置", padding=8)
        cfg_frame.pack(fill="x", pady=(0, 6))

        self._form = ConfigForm(cfg_frame, JellyfinNfoConfig)
        self._form.pack(fill="x")

        # ---- 按钮区 ----
        actions = ttk.Frame(root, padding=(0, 4))
        actions.pack(fill="x")
        ttk.Button(actions, text="保存配置",
                   command=self._on_save_config).pack(side="left")
        self._btn_extract = ttk.Button(
            actions, text="提取图片", command=self._on_extract)
        self._btn_extract.pack(side="left", padx=(8, 0))
        self._btn_generate = ttk.Button(
            actions, text="生成 NFO", command=self._on_generate)
        self._btn_generate.pack(side="left", padx=4)
        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(actions, textvariable=self._status_var).pack(
            side="left", padx=12)

        # ---- 表格 ----
        def _row_tags(r: dict) -> tuple:
            tag = r.get("tag")
            return (tag,) if tag else ()

        self.result_tree = SortableTreeview(
            root,
            key=lambda r: r["code"],
            height=6,
            searchable=True,
            columns=[
                Column("code", "番号", 140,
                       display=lambda r: r["code"],
                       sort=lambda r: r["code"].lower()),
                Column("videos", "视频数", 70,
                       display=lambda r: str(r["video_count"]),
                       sort=lambda r: r["video_count"],
                       anchor="center"),
                Column("cover", "封面", 60,
                       display=lambda r: "✓" if r["cover_ok"] else "—",
                       sort=lambda r: 1 if r["cover_ok"] else 0,
                       anchor="center"),
                Column("nfo", "NFO", 60,
                       display=lambda r: "✓" if r["nfo_ok"] else "—",
                       sort=lambda r: 1 if r["nfo_ok"] else 0,
                       anchor="center"),
                Column("status", "状态", 320,
                       display=lambda r: r["status"],
                       sort=lambda r: r["status"].lower(),
                       stretch=True),
            ],
            row_tags=_row_tags,
            tag_configure={
                "ok":     {"foreground": "#1a7f37"},
                "warn":   {"foreground": "#c77b00"},
                "failed": {"foreground": "#c0392b"},
            },
        )
        self.result_tree.pack(fill="both", expand=True, pady=(8, 0))

        # ---- 日志 ----
        ttk.Label(root, text="日志:").pack(anchor="w", pady=(6, 0))
        self._log = tk.Text(root, height=8, wrap="none", state="disabled")
        self._log.pack(fill="both", expand=True)

    # ============================================================
    # 配置保存
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
    # 提取图片
    # ============================================================
    def _on_extract(self) -> None:
        if self._working:
            return
        self._config = self._form.collect()
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

        if not self._config.input_excel:
            messagebox.showwarning("提示", "请先选择输入 Excel",
                                   parent=self._parent())
            return
        if not Path(self._config.input_excel).exists():
            messagebox.showerror("错误",
                                 f"文件不存在：{self._config.input_excel}",
                                 parent=self._parent())
            return
        if not self._config.cover_source_dir:
            messagebox.showwarning("提示", "请先选择封面源目录",
                                   parent=self._parent())
            return

        self._clear_log()
        self.result_tree.clear()
        self._working = True
        self._btn_extract.configure(state="disabled")
        self._btn_generate.configure(state="disabled")
        self._status_var.set("提取图片中…")

        threading.Thread(target=self._extract_worker, daemon=True).start()

    def _extract_worker(self) -> None:
        def log(msg):
            self._queue.put(("log", msg))

        try:
            exported, skipped = extract_covers_from_excel(
                excel_path=self._config.input_excel,
                sheet_index=self._config.sheet_index,
                code_column_name=self._config.code_column,
                cover_column_name=self._config.cover_column,
                header_row=self._config.header_row,
                output_dir=self._config.cover_source_dir,
                on_log=log,
            )
            self._queue.put(("extract_done", (exported, skipped)))
        except Exception as e:
            self._queue.put(("log", f"[错误] 提取失败: {e}"))
            self._queue.put(("log", traceback.format_exc()))
            self._queue.put(("extract_done", None))

    def _finish_extract(self, payload) -> None:
        self._working = False
        self._btn_extract.configure(state="normal")
        self._btn_generate.configure(state="normal")
        if payload is None:
            self._status_var.set("提取失败")
            return
        exported, skipped = payload
        self._status_var.set(f"提取完成：{exported} 张成功，{skipped} 张跳过")
        messagebox.showinfo(
            "提取完成",
            f"成功 {exported} 张，跳过 {skipped} 张\n\n输出到：\n"
            f"{self._config.cover_source_dir}",
            parent=self._parent(),
        )

    # ============================================================
    # 生成 NFO
    # ============================================================
    def _on_generate(self) -> None:
        if self._working:
            return
        self._config = self._form.collect()
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

        checks = [
            (self._config.input_excel, "输入 Excel"),
            (self._config.movie_root, "电影文件目录"),
        ]
        for value, label in checks:
            if not value:
                messagebox.showwarning("提示", f"请先选择{label}",
                                       parent=self._parent())
                return
        if not Path(self._config.input_excel).exists():
            messagebox.showerror("错误",
                                 f"文件不存在：{self._config.input_excel}",
                                 parent=self._parent())
            return
        if not Path(self._config.movie_root).exists():
            messagebox.showerror("错误",
                                 f"目录不存在：{self._config.movie_root}",
                                 parent=self._parent())
            return

        self._clear_log()
        self.result_tree.clear()
        self._working = True
        self._btn_extract.configure(state="disabled")
        self._btn_generate.configure(state="disabled")
        self._status_var.set("生成 NFO 中…")

        threading.Thread(target=self._nfo_worker, daemon=True).start()

    def _nfo_worker(self) -> None:
        def log(msg):
            self._queue.put(("log", msg))

        try:
            movie_data = read_excel_data(self._config)
            log(f"从 Excel 读取到 {len(movie_data)} 条电影数据")

            movie_root = Path(self._config.movie_root)
            processed = 0
            missing = 0
            total = 0

            for folder in sorted(movie_root.iterdir()):
                if not folder.is_dir():
                    continue
                code = folder.name.strip()
                total += 1

                if code not in movie_data:
                    log(f"跳过 {code}：未在 Excel 中找到")
                    self._queue.put(("row", {
                        "code": code, "video_count": 0,
                        "cover_ok": False, "nfo_ok": False,
                        "status": "未匹配", "tag": "warn",
                    }))
                    missing += 1
                    continue

                log(f"处理 {code}")
                result = organize_movie_folder(
                    folder, code, movie_data[code],
                    self._config, on_log=log,
                )
                processed += 1

                status_text = result["message"] or "完成"
                if not result["video_count"]:
                    tag = "warn"
                elif result["nfo_ok"]:
                    tag = "ok"
                else:
                    tag = "warn"

                self._queue.put(("row", {
                    "code": code,
                    "video_count": result["video_count"],
                    "cover_ok": result["cover_ok"],
                    "nfo_ok": result["nfo_ok"],
                    "status": status_text,
                    "tag": tag,
                }))

            self._queue.put(("nfo_done", (processed, missing, total)))
        except Exception as e:
            self._queue.put(("log", f"[错误] 生成失败: {e}"))
            self._queue.put(("log", traceback.format_exc()))
            self._queue.put(("nfo_done", None))

    def _finish_nfo(self, payload) -> None:
        self._working = False
        self._btn_extract.configure(state="normal")
        self._btn_generate.configure(state="normal")
        if payload is None:
            self._status_var.set("生成失败")
            return
        processed, missing, total = payload
        self._status_var.set(
            f"完成：{processed} 处理，{missing} 未匹配，共 {total}")
        messagebox.showinfo(
            "生成完成",
            f"共 {total} 个文件夹\n"
            f"处理成功 {processed} 个\n"
            f"未匹配 {missing} 个",
            parent=self._parent(),
        )

    # ============================================================
    # 队列消费
    # ============================================================
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "row":
                    self._insert_row(payload)
                elif kind == "extract_done":
                    self._finish_extract(payload)
                elif kind == "nfo_done":
                    self._finish_nfo(payload)
        except queue.Empty:
            pass
        if self._root is not None:
            self._root.after(80, self._poll)

    def _insert_row(self, r: dict) -> None:
        self.result_tree.append_row(r)

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
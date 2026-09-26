"""Jellyfin NFO 插件的 GUI。"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from av_scraper.plugin_api import PluginContext

from .config import JellyfinNfoConfig
from .excel_reader import read_excel_data
from .image_extractor import extract_covers_from_excel
from .organizer import organize_movie_folder

logger = logging.getLogger(__name__)


_NFO_MODES = {
    "每个视频生成同名 NFO": "per_video",
    "只生成 movie.nfo": "movie_nfo",
    "两种都生成": "both",
}
_NFO_MODES_REVERSE = {v: k for k, v in _NFO_MODES.items()}

_COVER_NAMINGS = {
    "两种都复制": "both",
    "与视频同名": "same_as_video",
    "用番号命名": "code",
}
_COVER_NAMINGS_REVERSE = {v: k for k, v in _COVER_NAMINGS.items()}


class JellyfinNfoPlugin:
    name = "Jellyfin NFO"
    version = "1.0.0"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self._config_path = self._resolve_config_path()
        self._config = JellyfinNfoConfig.load(self._config_path)
        self._queue: queue.Queue = queue.Queue()
        self._working = False
        self._root: Optional[ttk.Frame] = None

        # 显式声明：Pylance 才知道这些属性存在。
        # 这些控件在 _build() 里创建并赋值，这里只占位。
        self._var_code_col: tk.StringVar = tk.StringVar()
        self._var_cover_col: tk.StringVar = tk.StringVar()      # ← 新增
        self._var_title_col: tk.StringVar = tk.StringVar()
        self._var_actor_cols: tk.StringVar = tk.StringVar()
        self._var_rating_cols: tk.StringVar = tk.StringVar()
        self._var_genre_cols: tk.StringVar = tk.StringVar()
        self._var_personal_cols: tk.StringVar = tk.StringVar()
        self._var_user_cols: tk.StringVar = tk.StringVar()

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
        self._load_to_ui()
        root.after(80, self._poll)
        return root

    # ============================================================
    # UI
    # ============================================================
    def _build(self, root: ttk.Frame) -> None:
        # ---- 路径 ----
        paths = ttk.LabelFrame(root, text="路径", padding=8)
        paths.pack(fill="x", pady=(0, 6))
        self._var_input_excel = self._make_path_row(paths, "输入 Excel:", "file")
        self._var_movie_root = self._make_path_row(paths, "电影文件目录:", "dir")
        self._var_cover_dir = self._make_path_row(paths, "封面源目录:", "dir")

        # ---- Excel 解析 ----
        pf = ttk.LabelFrame(root, text="Excel 解析", padding=8)
        pf.pack(fill="x", pady=(0, 6))

        # 第一行：工作表 + 表头行 + 提示
        r0 = ttk.Frame(pf)
        r0.pack(fill="x", pady=(0, 6))
        ttk.Label(r0, text="第").pack(side="left")
        self._var_sheet_index = tk.StringVar()
        ttk.Spinbox(r0, from_=1, to=99, textvariable=self._var_sheet_index,
                    width=4).pack(side="left", padx=2)
        ttk.Label(r0, text="个工作表，第").pack(side="left")
        self._var_header_row = tk.StringVar()
        ttk.Spinbox(r0, from_=1, to=9999, textvariable=self._var_header_row,
                    width=5).pack(side="left", padx=2)
        ttk.Label(r0, text="行是列名").pack(side="left")
        ttk.Label(
            r0,
            text="（演员 / 评分 / 类别 / 个人评论 / 网友评论支持多列，用 , 分隔）",
            foreground="#888",
        ).pack(side="left", padx=(12, 0))

        # 第二、三行：字段网格（转置布局）
        grid = ttk.Frame(pf)
        grid.pack(fill="x")

        # (显示名, 实例变量名, 输入框宽度)
        fields = [
            ("番号",     "_var_code_col",      9),
            ("封面",     "_var_cover_col",     9),
            ("标题",     "_var_title_col",    10),
            ("演员",     "_var_actor_cols",   14),
            ("评分",     "_var_rating_cols",  10),
            ("类别",     "_var_genre_cols",   12),
            ("个人评论", "_var_personal_cols", 16),
            ("网友评论", "_var_user_cols",     16),
        ]

        ttk.Label(grid, text="提取的信息：").grid(
            row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        ttk.Label(grid, text="列名为：").grid(
            row=1, column=0, sticky="e", padx=(0, 6), pady=2)

        for i, (label, var_name, width) in enumerate(fields, start=1):
            ttk.Label(grid, text=label).grid(
                row=0, column=i, sticky="w", padx=2, pady=2)
            var = tk.StringVar()
            setattr(self, var_name, var)
            ttk.Entry(grid, textvariable=var, width=width).grid(
                row=1, column=i, sticky="ew", padx=2, pady=2)

        # ---- NFO 选项 ----
        opts = ttk.LabelFrame(root, text="NFO 选项", padding=8)
        opts.pack(fill="x", pady=(0, 6))

        r1 = ttk.Frame(opts); r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="NFO 模式:").pack(side="left")
        self._var_nfo_mode = tk.StringVar()
        ttk.Combobox(r1, textvariable=self._var_nfo_mode,
                     values=list(_NFO_MODES), state="readonly",
                     width=22).pack(side="left", padx=4)
        ttk.Label(r1, text="封面命名:").pack(side="left", padx=(16, 0))
        self._var_cover_naming = tk.StringVar()
        ttk.Combobox(r1, textvariable=self._var_cover_naming,
                     values=list(_COVER_NAMINGS), state="readonly",
                     width=14).pack(side="left", padx=4)

        r2 = ttk.Frame(opts); r2.pack(fill="x", pady=2)
        self._var_copy_cover = tk.BooleanVar()
        ttk.Checkbutton(r2, text="复制封面",
                        variable=self._var_copy_cover).pack(
            side="left", padx=(0, 12))
        self._var_overwrite = tk.BooleanVar()
        ttk.Checkbutton(r2, text="覆盖已有 NFO",
                        variable=self._var_overwrite).pack(side="left")

        # ---- 按钮 ----
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
        cols = ("code", "videos", "cover", "nfo", "status")
        self._tree = ttk.Treeview(root, columns=cols, show="headings", height=8)
        headers = {
            "code": "番号", "videos": "视频数",
            "cover": "封面", "nfo": "NFO", "status": "状态",
        }
        widths = {
            "code": 140, "videos": 70, "cover": 60,
            "nfo": 60, "status": 320,
        }
        for c in cols:
            self._tree.heading(c, text=headers[c])
            self._tree.column(c, width=widths[c], anchor="w",
                              stretch=(c == "status"))
        self._tree.tag_configure("ok", foreground="#1a7f37")
        self._tree.tag_configure("warn", foreground="#c77b00")
        self._tree.tag_configure("failed", foreground="#c0392b")
        self._tree.pack(fill="both", expand=True, pady=(8, 0))

        # ---- 日志 ----
        ttk.Label(root, text="日志:").pack(anchor="w", pady=(6, 0))
        self._log = tk.Text(root, height=8, wrap="none", state="disabled")
        self._log.pack(fill="both")

    def _make_path_row(self, parent, label: str, kind: str) -> tk.StringVar:
        row = ttk.Frame(parent); row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=14, anchor="e").pack(side="left")
        var = tk.StringVar()
        ttk.Entry(row, textvariable=var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="浏览…",
                   command=lambda v=var, k=kind: self._pick_path(v, k)).pack(
            side="left")
        return var

    # ============================================================
    # 配置读写
    # ============================================================
    def _load_to_ui(self) -> None:
        c = self._config
        self._var_input_excel.set(c.input_excel)
        self._var_movie_root.set(c.movie_root)
        self._var_cover_dir.set(c.cover_source_dir)
        self._var_sheet_index.set(str(c.sheet_index))
        self._var_header_row.set(str(c.header_row))
        self._var_code_col.set(c.code_column)
        self._var_cover_col.set(c.cover_column)      # ← 新增
        self._var_title_col.set(c.title_column)
        self._var_actor_cols.set(c.actor_columns)
        self._var_rating_cols.set(c.rating_columns)
        self._var_genre_cols.set(c.genre_columns)
        self._var_personal_cols.set(c.personal_comment_columns)
        self._var_user_cols.set(c.user_comment_columns)
        self._var_nfo_mode.set(
            _NFO_MODES_REVERSE.get(c.nfo_mode, "每个视频生成同名 NFO"))
        self._var_cover_naming.set(
            _COVER_NAMINGS_REVERSE.get(c.cover_naming, "两种都复制"))
        self._var_copy_cover.set(c.copy_cover)
        self._var_overwrite.set(c.overwrite_existing)

    def _ui_to_config(self) -> JellyfinNfoConfig:
        def _i(s, default):
            try:
                return int(s)
            except (TypeError, ValueError):
                return default

        return JellyfinNfoConfig(
            input_excel=self._var_input_excel.get().strip(),
            movie_root=self._var_movie_root.get().strip(),
            cover_source_dir=self._var_cover_dir.get().strip(),
            sheet_index=_i(self._var_sheet_index.get(), 1),
            header_row=_i(self._var_header_row.get(), 1),
            code_column=self._var_code_col.get().strip() or "番号",
            cover_column=self._var_cover_col.get().strip() or "封面",     # ← 新增
            title_column=self._var_title_col.get().strip() or "标题",
            actor_columns=self._var_actor_cols.get().strip(),
            rating_columns=self._var_rating_cols.get().strip(),
            genre_columns=self._var_genre_cols.get().strip(),
            personal_comment_columns=self._var_personal_cols.get().strip(),
            user_comment_columns=self._var_user_cols.get().strip(),
            nfo_mode=_NFO_MODES.get(self._var_nfo_mode.get(), "per_video"),
            cover_naming=_COVER_NAMINGS.get(
                self._var_cover_naming.get(), "both"),
            copy_cover=bool(self._var_copy_cover.get()),
            overwrite_existing=bool(self._var_overwrite.get()),
            recent_input_excels=list(self._config.recent_input_excels),
            recent_movie_roots=list(self._config.recent_movie_roots),
            recent_cover_dirs=list(self._config.recent_cover_dirs),
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
    def _pick_path(self, var: tk.StringVar, kind: str) -> None:
        cur = var.get().strip()
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=cur or None,
                                        parent=self._parent())
        elif kind == "file":
            init = str(Path(cur).parent) if cur else None
            p = filedialog.askopenfilename(
                initialdir=init,
                filetypes=[("Excel", "*.xlsx *.xlsm"), ("所有文件", "*.*")],
                parent=self._parent(),
            )
        else:
            return
        if p:
            var.set(p)

    # ============================================================
    # 提取图片
    # ============================================================
    def _on_extract(self) -> None:
        if self._working:
            return
        self._config = self._ui_to_config()
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
        self._tree.delete(*self._tree.get_children())
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
                cover_column_name=self._config.cover_column,      # ← 新增
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
        self._config = self._ui_to_config()
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
        self._tree.delete(*self._tree.get_children())
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
        cover_icon = "✓" if r["cover_ok"] else "—"
        nfo_icon = "✓" if r["nfo_ok"] else "—"
        self._tree.insert(
            "", "end",
            values=(r["code"], r["video_count"],
                    cover_icon, nfo_icon, r["status"]),
            tags=(r.get("tag", ""),),
        )
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])

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
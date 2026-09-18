"""主窗口。"""

from __future__ import annotations

import tkinter as tk
import logging

from pathlib import Path
from tkinter import ttk
from ..config import AppConfig
from .config_tab import ConfigTab
from .move_tab import MoveTab
from .scan_tab import ScanTab
from dataclasses import replace
from ..paths import log_dir
from ..plugin_api import PluginContext
from ..plugin_loader import discover_plugins, instantiate_plugin

logger = logging.getLogger(__name__)   # ← 加这一行

class App(tk.Tk):
    def __init__(self, config_path: Path):
        super().__init__()
        self.title("JAVscraper")
        self.geometry("1180x820")
        self.minsize(960, 640)

        self.config_path = config_path
        self.app_config = AppConfig.load(config_path)

        self._build()

    def _build(self) -> None:
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass
        
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        self.scan_tab = ScanTab(nb, self)
        self.move_tab = MoveTab(nb, self)
        self.config_tab = ConfigTab(nb, self)
        # self.notebook = nb
        nb.add(self.scan_tab, text="① 扫描")
        nb.add(self.move_tab, text="② 移动")
        nb.add(self.config_tab, text="③ 配置")

        self._load_plugins(nb)   # ★

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(
            self, textvariable=self.status_var, anchor="w",
            relief="sunken", padding=(6, 2),
        ).pack(fill="x", side="bottom")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    # 供 ConfigTab 保存后调用，让其他页读取新配置
    def refresh_from_config(self) -> None:
        self.scan_tab.sync_from_config()
        self.move_tab.sync_from_config()

    def _save_config(self) -> None:
        """供插件调用：把当前 app_config 写回磁盘。"""
        try:
            self.app_config.save(self.config_path)
        except OSError:
            pass

    def _load_plugins(self, notebook) -> None:
        specs = discover_plugins()
        if not specs:
            return

        base_ctx = PluginContext(
            app=self,
            app_config=self.app_config,
            config_path=self.config_path,
            log_dir=log_dir(),
            plugin_dir=Path(),          # 每个插件实例化时替换
            save_config=self._save_config,
        )

        tab_index = 4
        for spec in specs:
            if spec.error:
                notebook.add(
                    self._make_plugin_error_tab(spec),
                    text=f"插件: {spec.name}",
                )
                continue

            ctx = replace(base_ctx, plugin_dir=spec.dir)
            plugin = instantiate_plugin(spec, ctx)
            if plugin is None:
                notebook.add(
                    self._make_plugin_error_tab(spec, "实例化失败"),
                    text=f"插件: {spec.name}",
                )
                continue

            try:
                frame = plugin.create_tab(notebook)
            except Exception as e:
                logger.exception("插件 %s 创建标签页失败", spec.name)
                notebook.add(
                    self._make_plugin_error_tab(spec, f"create_tab 异常: {e}"),
                    text=f"插件: {spec.name}",
                )
                continue

            if frame is not None:
                notebook.add(frame, text=f"{tab_index} {plugin.name}")
                tab_index += 1

    def _make_plugin_error_tab(self, spec, extra: str = ""):
        frame = ttk.Frame(self, padding=20)
        ttk.Label(
            frame, text=f"插件「{spec.name}」无法加载",
            font=("", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        msg = spec.error or extra or "未知错误"
        ttk.Label(
            frame, text=msg, foreground="#c0392b",
            wraplength=800, justify="left",
        ).pack(anchor="w", pady=4)
        if spec.dependencies:
            hint = ("该插件需要以下依赖：\n  " +
                    "\n  ".join(spec.dependencies))
            ttk.Label(
                frame, text=hint, justify="left", foreground="#444",
            ).pack(anchor="w", pady=(12, 0))
        return frame
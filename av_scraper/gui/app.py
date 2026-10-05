"""主窗口。"""

from __future__ import annotations

import dataclasses
import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from dataclasses import replace

from ..config import AppConfig, ProcessorConfig, ScraperConfig
from ..paths import log_dir
from ..plugin_api import PluginContext
from ..plugin_loader import discover_plugins, instantiate_plugin
from ..widgets import ConfigForm
from .config_window import ConfigPage, ConfigWindow
from .move_tab import MoveTab
from .scan_tab import ScanTab

logger = logging.getLogger(__name__)

class App(tk.Tk):

    notebook: ttk.Notebook          # ← 加这一行

    def __init__(self, config_path: Path):
        super().__init__()
        self.title("JAVscraper")
        self.geometry("1180x820")
        self.minsize(960, 640)

        self.config_path = config_path
        self.app_config = AppConfig.load(config_path)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        """关闭窗口前的确认。开关由 config.confirm_on_close 控制。"""
        if getattr(self.app_config, "confirm_on_close", True):
            from tkinter import messagebox
            if not messagebox.askyesno(
                "确认关闭",
                "确定要关闭程序吗？\n\n"
                "如果有任务正在进行，会被中断，未保存的数据将丢失。",
                parent=self,
            ):
                return
        self.destroy()

    def _build(self) -> None:
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass

        # ===== 状态栏先 pack，优先占据底部空间 =====
        status_bar = ttk.Frame(self)
        status_bar.pack(fill="x", side="bottom")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(
            status_bar, textvariable=self.status_var, anchor="w",
            relief="sunken", padding=(6, 2),
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            status_bar, text="关于", width=6,
            command=self._show_about,
        ).pack(side="right", padx=(2, 2))
        ttk.Button(
            status_bar, text="配置", width=6,
            command=self._open_config_window,
        ).pack(side="right")

        # ===== Notebook 占据剩余空间 =====
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        self.scan_tab = ScanTab(nb, self)
        self.move_tab = MoveTab(nb, self)
        self.notebook = nb
        nb.add(self.scan_tab, text="1-扫描")
        nb.add(self.move_tab, text="2-移动")

        self._load_plugins(nb)   # ★

        # 插件加载完毕，统一应用一次布局
        self._apply_layout_all()

    def _show_about(self) -> None:
        from .dialogs import show_about
        from .. import __version__

        # 收集已加载插件
        plugins: list[tuple[str, str]] = []
        for plugin in getattr(self, "_loaded_plugins", []):
            name = getattr(plugin, "name", "?")
            version = getattr(plugin, "version", "?")
            plugins.append((name, version))

        show_about(self, plugins=plugins)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    # 供 ConfigTab 保存后调用，让其他页读取新配置
    def refresh_from_config(self) -> None:
        self.scan_tab.sync_from_config()
        self.move_tab.sync_from_config()
        for plugin in getattr(self, "_loaded_plugins", []):
            sync = getattr(plugin, "sync_from_config", None)
            if callable(sync):
                try:
                    sync()
                except Exception:
                    logger.exception("插件 %s 同步配置失败",
                                     getattr(plugin, "name", "?"))
        # 应用布局偏好
        self._apply_layout_all()

    def _apply_layout_all(self) -> None:
        widgets = [self.scan_tab, self.move_tab]
        for plugin in getattr(self, "_loaded_plugins", []):
            widgets.append(plugin)
        for w in widgets:
            fn = getattr(w, "apply_layout", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.exception("应用布局失败：%s",
                                     getattr(w, "name", "?"))

    def _save_config(self) -> None:
        """供插件调用：把当前 app_config 写回磁盘。"""
        try:
            self.app_config.save(self.config_path)
        except OSError:
            pass

    def _load_plugins(self, notebook) -> None:
        self._loaded_plugins = []
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

        tab_index = 3
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
                notebook.add(frame, text=f"{tab_index}-{plugin.name}")
                tab_index += 1
                self._loaded_plugins.append(plugin)

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

    # ============================================================
    # 配置窗口
    # ============================================================
    def _open_config_window(self) -> None:
        pages = self._core_config_pages()

        from typing import cast

        for plugin in getattr(self, "_loaded_plugins", []):
            getter = getattr(plugin, "config_pages", None)
            if not callable(getter):
                continue
            try:
                extra = cast("list[ConfigPage]", getter())
                pages.extend(extra)
            except Exception:
                logger.exception("插件 %s 提供配置页失败", getattr(plugin, "name", "?"))

        try:
            ConfigWindow(self, pages)
        except Exception:
            logger.exception("打开配置窗口失败")

    def _core_config_pages(self) -> list[ConfigPage]:
        """核心配置页：刮削器 / 文件处理器 / 应用 三段叠在一个 Tab 里。"""
        forms: dict[str, ConfigForm] = {}

        def build(parent: ttk.Frame) -> ttk.Frame:
            frame = ttk.Frame(parent)
            sections = [
                ("scraper", "刮削器", ScraperConfig),
                ("processor", "文件处理器", ProcessorConfig),
                ("app", "应用", AppConfig),
            ]
            for key, title, cls in sections:
                group = ttk.LabelFrame(frame, text=title, padding=8)
                group.pack(fill="x", pady=6)
                form = ConfigForm(group, cls)
                form.pack(fill="x")
                forms[key] = form
            return frame

        def load(cfg: AppConfig) -> None:
            forms["scraper"].load_from(cfg.scraper)
            forms["processor"].load_from(cfg.processor)
            forms["app"].load_from(cfg)

        def collect() -> AppConfig:
            new = AppConfig()
            new.scraper = forms["scraper"].collect()
            new.processor = forms["processor"].collect()
            new_app = forms["app"].collect()
            for f in dataclasses.fields(AppConfig):
                if f.name in ("scraper", "processor"):
                    continue
                setattr(new, f.name, getattr(new_app, f.name))
            return new

        def save(cfg: AppConfig) -> None:
            # 原地更新，保持 self.app_config 的对象身份（插件持有引用）
            self.app_config.scraper = cfg.scraper
            self.app_config.processor = cfg.processor
            for f in dataclasses.fields(AppConfig):
                if f.name in ("scraper", "processor"):
                    continue
                setattr(self.app_config, f.name, getattr(cfg, f.name))
            self.app_config.save(self.config_path)
            self.refresh_from_config()

        def current() -> AppConfig:
            return self.app_config

        def default() -> AppConfig:
            return AppConfig()

        return [ConfigPage(
            title="核心配置",
            build=build,
            load=load,
            collect=collect,
            save=save,
            current=current,
            default=default,
        )]
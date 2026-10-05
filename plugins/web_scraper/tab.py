"""Web Scraper 插件：整合多个爬取源。"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

from av_scraper.plugin_api import PluginContext

from .config import ScraperPluginConfig
from .sources import SOURCES

logger = logging.getLogger(__name__)


class WebScraperPlugin:
    name = "爬虫"
    version = "1.1"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.config_path = self._resolve_config_path()
        self.config = ScraperPluginConfig.load(self.config_path)
        self._sources: list = []
        self._root: Optional[ttk.Frame] = None

    def _resolve_config_path(self) -> Path:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and str(self.ctx.plugin_dir).startswith(str(meipass)):
            return Path(sys.executable).resolve().parent / "scraper_config.json"
        return self.ctx.plugin_dir / "config.json"

    def save_config(self) -> None:
        try:
            self.config.save(self.config_path)
        except OSError:
            pass

    # ---------- 插件协议 ----------
    def create_tab(self, notebook) -> ttk.Frame:
        root = ttk.Frame(notebook)
        self._root = root

        inner = ttk.Notebook(root)
        inner.pack(fill="both", expand=True)

        for SourceCls in SOURCES:
            try:
                source = SourceCls(self, self.ctx)
            except Exception:
                logger.exception(
                    "源 %s 初始化失败", getattr(SourceCls, "__name__", "?"))
                continue
            try:
                frame = source.build_tab(inner)
            except Exception:
                logger.exception(
                    "源 %s 构建 UI 失败", getattr(source, "name", "?"))
                continue
            if frame is not None:
                inner.add(frame, text=source.name)
                self._sources.append(source)

        return root

    def config_pages(self):
        pages = []
        for source in self._sources:
            getter = getattr(source, "config_page", None)
            if not callable(getter):
                continue
            try:
                page = getter()
            except Exception:
                logger.exception(
                    "源 %s 获取配置页失败", getattr(source, "name", "?"))
                continue
            if page is not None:
                pages.append(page)
        return pages

    def sync_from_config(self) -> None:
        for source in self._sources:
            sync = getattr(source, "sync_from_config", None)
            if callable(sync):
                try:
                    sync()
                except Exception:
                    logger.exception(
                        "源 %s 同步配置失败", getattr(source, "name", "?"))
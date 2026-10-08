"""爬取源注册表。

每个源是独立单元，提供自己的 UI Tab 和配置页。
顶层插件通过 SOURCES 列表加载所有源。
"""

from __future__ import annotations

from typing import Any, Protocol

from av_scraper.gui.config_window import ConfigPage


class Source(Protocol):
    """一个爬取源的协议。

    - name:           显示名，作为内部 Notebook 的 Tab 标题
    - build_tab:      构建 UI，返回一个 ttk.Frame
    - config_page:    返回配置页，或 None（不参与统一配置窗口）
    - is_busy:        是否有任务正在进行
    - sync_from_config: 核心调用，通知源刷新 UI（可选实现）
    """

    name: str

    def __init__(self, plugin: Any, ctx: Any) -> None: ...

    def build_tab(self, parent: Any) -> Any: ...

    def config_page(self) -> ConfigPage | None: ...

    def is_busy(self) -> bool: ...

    def sync_from_config(self) -> None: ...


# ---------- 源注册 ----------
from .javdb.source import JavdbSource
from .subtitlecat.source import SubtitleCatSource

SOURCES: list[type] = [
    JavdbSource,
    SubtitleCatSource,
]
"""插件接口定义。

核心不 import 插件的任何业务模块，只通过本文件约定接口。
插件可以 import 本模块（因为它属于核心包）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass
class PluginContext:
    """核心传给插件的运行时上下文。"""
    app: Any                          # 主窗口（tk.Tk 实例）
    app_config: Any                   # AppConfig 实例
    config_path: Path                 # config.json 路径（可写）
    log_dir: Path                     # log/ 目录（可写）
    plugin_dir: Path                  # 插件所在目录（只读，仅用于定位自身资源）
    save_config: Callable[[], None]   # 触发核心保存配置


class Plugin(Protocol):
    name: str
    version: str

    def __init__(self, ctx: PluginContext) -> None: ...

    def create_tab(self, notebook: Any) -> Any:
        """返回一个 tkinter Frame；返回 None 则不添加标签页。"""
        ...
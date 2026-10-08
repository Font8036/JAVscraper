"""应用路径定位：区分源码运行与打包运行。"""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """项目根目录。

    - 打包后（PyInstaller）：exe 同级目录
    - 源码运行：av_scraper 包的上一层（即 run.py / README.md 所在目录）
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # paths.py -> av_scraper/ -> 项目根
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / "config.json"


def log_dir() -> Path:
    """日志目录：app_dir()/log，首次访问自动创建。"""
    d = app_dir() / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d

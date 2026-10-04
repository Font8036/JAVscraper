"""完整版入口：内置 javdb 插件一起打包。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

# 让 PyInstaller 静态分析到所有插件的依赖。
# 不要用 try/except 吞异常——打包时如果 import 失败要显式报错，
# 否则 PyInstaller 会静默跳过整个插件的依赖分析。
import javdb.tab        # noqa: F401
import javdb.fetch      # noqa: F401
import javdb.parse      # noqa: F401
import javdb.report     # noqa: F401
import javdb.config     # noqa: F401

import jellyfin_nfo.tab            # noqa: F401
import jellyfin_nfo.config         # noqa: F401
import jellyfin_nfo.parser         # noqa: F401
import jellyfin_nfo.excel_reader   # noqa: F401
import jellyfin_nfo.image_extractor  # noqa: F401
import jellyfin_nfo.nfo_builder    # noqa: F401
import jellyfin_nfo.organizer      # noqa: F401

# 打包命令
# conda activate private
# cd /d C:\Disk\C\applicationdata\coding\JAVscraper
# pyinstaller -D -w -n JAVscraper_full_v0.3.16_win64 --paths . --paths plugins --add-data "plugins;plugins" --collect-all playwright --clean run_full.py

from av_scraper.__main__ import main

if __name__ == "__main__":
    main()
"""顶层入口：直接 python run.py 或双击运行。

不带参数启动图形界面；带参数走命令行：

    python run.py scan D:\\videos
    python run.py --help
"""

import sys
from pathlib import Path

# 保证能 import 到 av_scraper 包，无论从哪里双击
sys.path.insert(0, str(Path(__file__).resolve().parent))

from av_scraper.__main__ import main

# 打包命令
# conda activate private
# cd /d C:\Disk\C\applicationdata\coding\JAVscraper
# pyinstaller -D -w -n JAVscraper_core_v0.5.0_win64 --icon=assets/icon.ico --add-data "assets;assets" --paths . --clean run.py

if __name__ == "__main__":
    raise SystemExit(main())

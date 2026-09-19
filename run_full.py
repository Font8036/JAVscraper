"""完整版入口：内置 javdb 插件一起打包。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

# 让 PyInstaller 收集插件依赖。失败不致命，便于源码调试。
try:
    import javdb.tab        # noqa: F401
    import javdb.fetch      # noqa: F401
    import javdb.parse      # noqa: F401
    import javdb.report     # noqa: F401
    import javdb.config     # noqa: F401
except ImportError:
    pass

# 打包命令
# conda activate private
# cd /d C:\Disk\C\applicationdata\coding\JAVscraper
# pyinstaller -D -w -n av-scraper-full --paths . --paths plugins --add-data "plugins;plugins" --collect-all playwright --clean run_full.py

from av_scraper.__main__ import main

if __name__ == "__main__":
    main()
"""命令行的顶层入口（给控制台版 exe 用）。

    pyinstaller -D -c -n JAVscraper_cli --paths . --clean run_cli.py

和 run.py 的区别只有两点：

  - 只 import 命令行那套（不 import 图形界面），所以打出来的 exe 里没有 Tk，
    体积更小，也不会在没装图形环境的地方出问题
  - 不带参数时打印用法，而不是启动窗口——这个 exe 存在的意义就是命令行
"""

import sys
from pathlib import Path

# 保证能 import 到 av_scraper 包，无论从哪里双击
sys.path.insert(0, str(Path(__file__).resolve().parent))

from av_scraper.cli import run

if __name__ == "__main__":
    raise SystemExit(run())

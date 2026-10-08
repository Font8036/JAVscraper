"""导入冒烟：把 av_scraper 和两个插件下的每个模块都 import 一遍。

    python test/import_smoke.py

为什么需要它：``from __future__ import annotations`` 让所有注解都变成字符串，
写错名字的注解**不会**在 import 时报错，pytest 也照样过。所以批量改注解
（如 UP045 的 ``Optional[X]`` → ``X | None``）或重排 import 之后，要用这个脚本
把每个模块真的走一遍。GUI 的 app / config_window / dialogs / sortable_treeview
平时没有测试直接 import，是最容易漏的地方。

文件名不是 ``test_*.py``，所以 pytest 不会收集它。

只 import，不创建任何窗口。
"""

import importlib
import pkgutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "plugins"))

import av_scraper  # noqa: E402

bad: list[str] = []
count = 0


def import_tree(package, prefix: str) -> None:
    global count
    for mod in pkgutil.walk_packages(package.__path__, prefix):
        count += 1
        try:
            importlib.import_module(mod.name)
        except Exception as e:  # noqa: BLE001 - 冒烟脚本，什么异常都要报出来
            bad.append(f"{mod.name}: {type(e).__name__}: {e}")


import_tree(av_scraper, "av_scraper.")

for name in ("web_scraper", "jellyfin_nfo"):
    try:
        pkg = importlib.import_module(name)
    except Exception as e:  # noqa: BLE001
        bad.append(f"{name}: {type(e).__name__}: {e}")
        continue
    import_tree(pkg, name + ".")

print(f"imported {count} modules")
print("\n".join(bad) if bad else "OK: 全部模块导入成功")
sys.exit(1 if bad else 0)

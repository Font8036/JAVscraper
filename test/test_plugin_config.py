"""三个插件配置类的读写测试。

插件配置写在插件自己的目录下，核心的 ``test_config.py`` 不管它们。三个 ``save()``
现在都走 ``av_scraper.fileio.write_via_temp``（临时文件 + 原子替换），所以要验证两件事：

  - 能正常往返（不会写出坏 JSON、不会留垃圾文件）
  - **写到一半失败时，磁盘上原来那份配置还在**，而且不留 ``.saving`` 临时文件

import 放在用例内部：``import web_scraper.config`` 会顺着 ``sources/__init__.py``
把 Tk 和 playwright 一起拉进来，没必要在收集阶段就付这个代价。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

CASES = [
    ("web_scraper.config", "ScraperPluginConfig"),
    ("jellyfin_nfo.config", "JellyfinNfoConfig"),
    ("web_scraper.sources.javdb.config", "JavdbSourceConfig"),
]
IDS = ["web_scraper", "jellyfin_nfo", "javdb"]


def load_class(module: str, name: str):
    return getattr(importlib.import_module(module), name)


def entries(directory: Path) -> list[str]:
    """目录下所有条目（含点开头的临时文件）。"""
    return sorted(p.name for p in directory.iterdir())


@pytest.mark.parametrize(("module", "name"), CASES, ids=IDS)
def test_plugin_config_round_trip(work_dir, module, name):
    cls = load_class(module, name)
    path = work_dir / "sub" / f"{name}.json"       # 父目录不存在，save 要自己建

    cls().save(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data
    assert type(cls.load(path)) is cls
    assert entries(path.parent) == [path.name]


@pytest.mark.parametrize(("module", "name"), CASES, ids=IDS)
def test_plugin_config_overwrite_leaves_no_temp_file(work_dir, module, name):
    cls = load_class(module, name)
    path = work_dir / f"{name}.json"

    cls().save(path)
    cls().save(path)                                # 覆盖已有配置

    assert entries(work_dir) == [path.name]         # 没有残留的 .xxx.saving


def test_plugin_config_failed_save_keeps_the_old_file(work_dir, monkeypatch):
    """原子写的意义：替换失败时旧配置必须完好，临时文件必须清掉。"""
    cls = load_class(*CASES[0])
    path = work_dir / "conf.json"
    cls().save(path)
    original = path.read_text(encoding="utf-8")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr("av_scraper.fileio.os.replace", refuse)

    with pytest.raises(OSError):
        cls().save(path)

    assert path.read_text(encoding="utf-8") == original
    assert entries(work_dir) == [path.name]

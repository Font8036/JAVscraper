"""爬虫插件队列轮询的测试（不建窗口）。

要防的是这个场景：保存失败时弹窗处置，而 Tk 弹窗会开启嵌套事件循环，
``root.after`` 排的 ``_poll`` 会在弹窗期间再次被调用 —— 如果不挡住重入，
排在后面的消息（例如字幕猫的总结）会在用户还没做选择时就被处理掉，
表现为"总结弹窗叠在处置弹窗上、内容还超前"。

这里用 ``object.__new__`` 跳过 ``__init__``，只装配 ``_poll`` 用到的几个属性，
所以不需要 Tk、也不需要插件的完整上下文。
"""

from __future__ import annotations

import queue

import pytest
from web_scraper.sources.javdb.source import JavdbSource
from web_scraper.sources.subtitlecat.source import SubtitleCatSource


def make_source(cls):
    """装配一个够用的实例：一个队列、一个终止用的 _root=None、以及被挡住的重入标记。"""
    src = object.__new__(cls)
    src._queue = queue.Queue()
    src._root = None                 # 不再重排 after，测试里手动驱动
    src._polling = False
    return src


@pytest.mark.parametrize("cls", [JavdbSource, SubtitleCatSource])
def test_poll_handles_messages_in_order(cls):
    src = make_source(cls)
    seen: list[str] = []
    src._append_log = seen.append
    src._queue.put(("log", "one"))
    src._queue.put(("log", "two"))

    src._poll()

    assert seen == ["one", "two"]


@pytest.mark.parametrize("cls", [JavdbSource, SubtitleCatSource])
def test_poll_ignores_reentrant_call(cls):
    """处理第一条消息时被"弹窗"重入：重入那次必须立刻返回，不抢先处理后面的消息。"""
    src = make_source(cls)
    seen: list[str] = []
    seen_at_reentry: list[int] = []

    def log(msg: str) -> None:
        seen.append(msg)
        if msg == "one":
            src._poll()                     # 模拟弹窗期间的 after 回调
            seen_at_reentry.append(len(seen))

    src._append_log = log
    src._queue.put(("log", "one"))
    src._queue.put(("log", "two"))

    src._poll()

    assert seen == ["one", "two"]           # 两条都处理了，但是先后关系
    assert seen_at_reentry == [1]           # 重入时没有把 "two" 也吃掉


@pytest.mark.parametrize("cls", [JavdbSource, SubtitleCatSource])
def test_poll_keeps_running_after_handler_error(cls):
    """某个处理函数抛错不能让轮询永久停掉（否则界面就"死"在那里）。"""
    src = make_source(cls)
    seen: list[str] = []

    def log(msg: str) -> None:
        if msg == "boom":
            raise RuntimeError("处理消息时出错")
        seen.append(msg)

    src._append_log = log
    src._queue.put(("log", "boom"))
    src._queue.put(("log", "after-boom"))

    with pytest.raises(RuntimeError):
        src._poll()

    assert src._polling is False            # 重入标记被复位
    src._poll()                             # 还能继续工作
    assert seen == ["after-boom"]

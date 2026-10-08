"""av_scraper.widgets.save_guard 的处置状态机测试（不建窗口）。

真实弹窗没法在测试里点，所以 ask / choose_path / timestamp 全部注入假的，
把「重试 / 换文件名 / 另存为 / 关闭窗口」每条分支都走一遍；
另外用真占用夹具模拟"用户去把 Excel 关掉再点重试"这条最关键的路径。
"""

from __future__ import annotations

import errno
from datetime import datetime
from pathlib import Path

import pytest

from av_scraper import fileio
from av_scraper.widgets import save_guard

FIXED_NOW = datetime(2026, 10, 9, 15, 30, 12)


def make_ask(*choices):
    """假的弹窗：按脚本依次返回选择，并记录每次被问到的目标与原因分类。"""
    calls: list[tuple[Path, fileio.SaveFailure]] = []

    def ask(parent, target, exc):
        calls.append((Path(target), fileio.classify_save_error(exc)))
        index = len(calls) - 1
        return choices[index] if index < len(choices) else None

    return ask, calls


def fixed_timestamp(target):
    return fileio.timestamped_path(target, now=FIXED_NOW)


@pytest.fixture
def report(work_dir):
    """(target, temp)：目标文件已有一份旧内容，临时文件里是新内容。"""
    target = work_dir / "报告.xlsx"
    target.write_text("OLD", encoding="utf-8")
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")
    return target, temp


# ---------------------------------------------------------------------------
# 顺利的情况不该打扰用户
# ---------------------------------------------------------------------------
def test_publishes_without_asking(work_dir):
    target = work_dir / "报告.xlsx"
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")

    ask, calls = make_ask("rename")
    assert save_guard.resolve_locked_target(None, temp, target, ask=ask) == target
    assert calls == []                                   # 一次都没问


# ---------------------------------------------------------------------------
# 换文件名
# ---------------------------------------------------------------------------
def test_rename_when_locked(report, locker):
    target, temp = report
    locker.lock(target)

    ask, calls = make_ask("rename")
    result = save_guard.resolve_locked_target(
        None, temp, target, ask=ask, timestamp=fixed_timestamp)
    locker.release()

    assert result.name == "报告_20261009_153012.xlsx"
    assert result.read_text(encoding="utf-8") == "NEW"
    assert target.read_text(encoding="utf-8") == "OLD"           # 原文件没被动
    assert calls == [(target, fileio.SaveFailure.LOCKED)]        # 原因报得对
    assert not temp.exists()                                     # 临时文件已发布


def test_retry_then_rename(report, locker):
    """用户先点重试（但文件还开着），再选换名 —— 两次都会被问到。"""
    target, temp = report
    locker.lock(target)

    ask, calls = make_ask("retry", "rename")
    result = save_guard.resolve_locked_target(
        None, temp, target, ask=ask, timestamp=fixed_timestamp)
    locker.release()

    assert len(calls) == 2
    assert all(failure is fileio.SaveFailure.LOCKED for _t, failure in calls)
    assert result.name == "报告_20261009_153012.xlsx"


def test_retry_after_user_closes_the_file(report, locker):
    """最关键的路径：用户去把 Excel 关掉，再点重试 → 写回原路径。"""
    target, temp = report
    locker.lock(target)

    def ask(parent, target_, exc):
        locker.release()                                  # 用户关掉了占用它的程序
        return "retry"

    assert save_guard.resolve_locked_target(None, temp, target, ask=ask) == target
    assert target.read_text(encoding="utf-8") == "NEW"
    assert not temp.exists()


# ---------------------------------------------------------------------------
# 另存为
# ---------------------------------------------------------------------------
def test_save_as_uses_chosen_path(report, locker):
    target, temp = report
    other = target.parent / "别处.xlsx"
    locker.lock(target)

    ask, _calls = make_ask("save_as")
    result = save_guard.resolve_locked_target(
        None, temp, target, ask=ask, choose_path=lambda _p, _t: str(other))
    locker.release()

    assert result == other
    assert other.read_text(encoding="utf-8") == "NEW"
    assert target.read_text(encoding="utf-8") == "OLD"


def test_save_as_cancelled_falls_back_to_asking_again(report, locker):
    """另存为对话框被取消（返回 None）→ 回到弹窗继续问，而不是直接放弃。"""
    target, temp = report
    locker.lock(target)

    ask, calls = make_ask("save_as", "rename")
    result = save_guard.resolve_locked_target(
        None, temp, target, ask=ask,
        choose_path=lambda _p, _t: None, timestamp=fixed_timestamp)
    locker.release()

    assert len(calls) == 2
    assert result.name == "报告_20261009_153012.xlsx"


# ---------------------------------------------------------------------------
# 关闭窗口（没有取消按钮，关窗口就是退出）
# ---------------------------------------------------------------------------
def test_closing_dialog_keeps_generated_content(report, locker):
    target, temp = report
    locker.lock(target)

    ask, _calls = make_ask(None)                          # X / Esc
    assert save_guard.resolve_locked_target(None, temp, target, ask=ask) is None
    locker.release()

    assert temp.exists()                                  # 生成好的内容没丢
    assert temp.read_text(encoding="utf-8") == "NEW"
    assert target.read_text(encoding="utf-8") == "OLD"


# ---------------------------------------------------------------------------
# 其它写失败（比如磁盘满）走同一套处置
# ---------------------------------------------------------------------------
def test_disk_full_offers_the_same_choices(work_dir, monkeypatch):
    target = work_dir / "报告.xlsx"
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")
    elsewhere = work_dir / "别的盘.xlsx"

    real_publish = save_guard.publish

    def fake_publish(src, dst):
        if Path(dst) == target:                           # 只有原目标写不进去
            raise OSError(errno.ENOSPC, "磁盘空间不足")
        return real_publish(src, dst)

    monkeypatch.setattr(save_guard, "publish", fake_publish)
    ask, calls = make_ask("save_as")

    result = save_guard.resolve_locked_target(
        None, temp, target, ask=ask,
        choose_path=lambda _p, _t: str(elsewhere))

    assert calls[0][1] is fileio.SaveFailure.NO_SPACE     # 弹窗能说清是空间不足
    assert result == elsewhere
    assert elsewhere.read_text(encoding="utf-8") == "NEW"

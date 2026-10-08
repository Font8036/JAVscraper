"""av_scraper.fileio 的单元测试。

其中"目标被占用"的用例用的是 test/conftest.py 里的真占用夹具（ctypes
``dwShareMode=0``），不是 mock —— 因为这套设计的核心事实（``os.replace``
在占用面前同样会失败）只能靠真实占用验证。
"""

from __future__ import annotations

import errno
from datetime import datetime
from pathlib import Path

import pytest

from av_scraper import fileio


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------
class _WrappedError(Exception):
    """模仿 xlsxwriter 的 FileCreateError：把 OSError 包在 args[0] 里。"""


@pytest.mark.parametrize("exc, expected", [
    (PermissionError(errno.EACCES, "denied"), fileio.SaveFailure.LOCKED),
    (PermissionError(errno.EPERM, "denied"), fileio.SaveFailure.LOCKED),
    (OSError(errno.ENOSPC, "no space"), fileio.SaveFailure.NO_SPACE),
    (ValueError("unrelated"), fileio.SaveFailure.OTHER),
    (_WrappedError(PermissionError(errno.EACCES, "denied")),
     fileio.SaveFailure.LOCKED),
    (_WrappedError(ValueError("inner")), fileio.SaveFailure.OTHER),
])
def test_classify_save_error(exc, expected):
    assert fileio.classify_save_error(exc) is expected


def test_classify_by_winerror():
    """os.replace 撞上占用时给的是 WinError 5 / 32，errno 不一定可靠。"""
    class SharingViolation(OSError):
        winerror = 32

    class AccessDenied(OSError):
        winerror = 5

    assert fileio.classify_save_error(SharingViolation(0, "x")) is fileio.SaveFailure.LOCKED
    assert fileio.classify_save_error(AccessDenied(0, "x")) is fileio.SaveFailure.LOCKED


# ---------------------------------------------------------------------------
# 临时文件与替换
# ---------------------------------------------------------------------------
def test_temp_path_sits_next_to_target(work_dir):
    target = work_dir / "报告.xlsx"
    temp = fileio.temp_path_for(target)
    assert temp.parent == target.parent          # 同目录才能原子替换
    assert temp != target
    assert target.name in temp.name


def test_write_via_temp_writes_target(work_dir):
    target = work_dir / "sub" / "config.json"    # 顺带验证父目录会被建出来
    result = fileio.write_via_temp(
        target, lambda p: p.write_text("内容", encoding="utf-8"))
    assert result == target
    assert target.read_text(encoding="utf-8") == "内容"
    assert not fileio.temp_path_for(target).exists()


def test_write_via_temp_keeps_old_file_on_failure(work_dir):
    target = work_dir / "config.json"
    target.write_text("OLD", encoding="utf-8")

    def boom(path: Path) -> None:
        path.write_text("半截内容", encoding="utf-8")
        raise ValueError("写到一半出错")

    with pytest.raises(ValueError):
        fileio.write_via_temp(target, boom)

    assert target.read_text(encoding="utf-8") == "OLD"        # 旧文件毫发无损
    assert not fileio.temp_path_for(target).exists()          # 临时文件已清理


def test_discard_removes_file(work_dir):
    path = work_dir / "x.tmp"
    path.write_text("x", encoding="utf-8")
    fileio.discard(path)
    assert not path.exists()


def test_discard_swallows_errors(work_dir, locker):
    """删不掉也不能抛错：worker 线程里的清理失败不该让任务无声死掉。"""
    path = work_dir / "x.tmp"
    path.write_text("x", encoding="utf-8")

    locker.lock(path)
    fileio.discard(path)                                  # 不该抛异常
    locker.release()

    assert path.exists()


def test_publish_replaces_existing_file(work_dir):
    target = work_dir / "a.txt"
    target.write_text("OLD", encoding="utf-8")
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")

    assert fileio.publish(temp, target) == target
    assert target.read_text(encoding="utf-8") == "NEW"
    assert not temp.exists()


# ---------------------------------------------------------------------------
# 备选文件名
# ---------------------------------------------------------------------------
def test_timestamped_path_uses_original_name_plus_timestamp(work_dir):
    target = work_dir / "报告.xlsx"
    now = datetime(2026, 10, 9, 15, 30, 12)
    assert fileio.timestamped_path(target, now=now).name == "报告_20261009_153012.xlsx"


def test_timestamped_path_avoids_existing_name(work_dir):
    target = work_dir / "报告.xlsx"
    now = datetime(2026, 10, 9, 15, 30, 12)
    first = fileio.timestamped_path(target, now=now)
    first.write_text("占位", encoding="utf-8")

    assert fileio.timestamped_path(target, now=now).name == "报告_20261009_153012_2.xlsx"


# ---------------------------------------------------------------------------
# 真实占用下的行为
# ---------------------------------------------------------------------------
def test_publish_fails_when_target_is_locked(work_dir, locker):
    """核心事实：目标被占用时，原子替换一样失败（这就是必须有弹窗的原因）。"""
    target = work_dir / "report.xlsx"
    target.write_text("OLD", encoding="utf-8")
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")

    locker.lock(target)
    with pytest.raises(OSError) as caught:
        fileio.publish(temp, target)
    assert fileio.classify_save_error(caught.value) is fileio.SaveFailure.LOCKED
    locker.release()

    assert target.read_text(encoding="utf-8") == "OLD"   # 旧文件没被破坏
    assert temp.exists()                                 # 临时文件还在，可以直接重试


def test_publish_to_other_name_works_while_locked(work_dir, locker):
    """“换文件名保存”能救场的根据。"""
    target = work_dir / "report.xlsx"
    target.write_text("OLD", encoding="utf-8")
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")

    locker.lock(target)
    other = fileio.timestamped_path(target)
    assert fileio.publish(temp, other).read_text(encoding="utf-8") == "NEW"
    locker.release()

    assert target.read_text(encoding="utf-8") == "OLD"    # 被占用的原文件没动


def test_publish_to_other_name_works_after_release(work_dir, locker):
    """用户关掉 Excel 之后，原来的目标路径也能写进去了。"""
    target = work_dir / "report.xlsx"
    target.write_text("OLD", encoding="utf-8")
    temp = fileio.temp_path_for(target)
    temp.write_text("NEW", encoding="utf-8")

    locker.lock(target)
    with pytest.raises(OSError):
        fileio.publish(temp, target)
    locker.release()

    assert fileio.publish(temp, target).read_text(encoding="utf-8") == "NEW"

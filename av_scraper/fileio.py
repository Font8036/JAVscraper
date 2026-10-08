"""文件写入的公共处理：原子替换、占用识别、备选文件名。

纯标准库、不依赖 Tk，核心与插件都能用：

  - ``write_via_temp()``：先写同目录临时文件，再替换到目标，避免"写到一半崩了"
    留下半截文件（配置、报告导出这类"用户不会开着"的文件用它就够）
  - ``temp_path_for()`` / ``publish()``：拆开的两步。会生成给用户看的文件
    （例如爬虫的 xlsx）请用这两个，好在替换失败时接上"占用处置"弹窗，
    这样重试只需要重做替换、不必重新生成
  - ``classify_save_error()``：把写失败归一成"被占用 / 磁盘满 / 其它"，
    好让上层用人话告诉用户，而不是糊一串 errno 和 traceback
  - ``timestamped_path()``：目标被占用时的备选名（原名 + 时间戳）

Windows 的一个硬事实（本项目实测过）：目标文件被其它程序独占打开时，
**``os.replace`` 一样会失败**（WinError 5 / 32）。原子替换解决的是"别把旧文件写坏"，
不是"绕过占用"；占用只能交给用户处置（重试 / 换文件名 / 另存为）。
"""

from __future__ import annotations

import errno
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

# 临时文件的附加后缀，例如 报告.xlsx → .报告.xlsx.saving
TEMP_SUFFIX = ".saving"

# 共享冲突 / 拒绝访问：都按"被占用"处理，因为在这套流程里它们的最常见成因相同
_LOCKED_WINERRORS = (5, 32)


class SaveFailure(Enum):
    """写失败的原因分类。"""

    LOCKED = "locked"        # 被其它程序占用，或没有写权限
    NO_SPACE = "no_space"    # 磁盘空间不足
    OTHER = "other"


def classify_save_error(exc: BaseException) -> SaveFailure:
    """把写失败归类。

    xlsxwriter 会把底层的 PermissionError 包成 ``FileCreateError``（不是 OSError
    子类），所以这里统一沿着 ``args[0]`` 往下找一层真正的 OSError。
    """
    cause: BaseException = exc
    for _ in range(3):
        if isinstance(cause, OSError):
            break
        if not (cause.args and isinstance(cause.args[0], BaseException)):
            break
        cause = cause.args[0]

    if isinstance(cause, OSError):
        if cause.errno == errno.ENOSPC:
            return SaveFailure.NO_SPACE
        if cause.errno in (errno.EACCES, errno.EPERM):
            return SaveFailure.LOCKED
        if getattr(cause, "winerror", None) in _LOCKED_WINERRORS:
            return SaveFailure.LOCKED
    return SaveFailure.OTHER


def temp_path_for(target: str | Path) -> Path:
    """目标同目录下的临时文件路径（必须同目录，``os.replace`` 才是原子的）。"""
    target = Path(target)
    return target.with_name(f".{target.name}{TEMP_SUFFIX}")


def publish(temp: str | Path, target: str | Path) -> Path:
    """把临时文件替换到目标位置，返回目标路径。占用等原因导致的失败原样抛出。"""
    temp, target = Path(temp), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(temp), str(target))
    return target


def discard(path: str | Path) -> None:
    """尽力删掉一个文件（例如生成失败的临时文件）；删不掉就算了，不抛错。

    worker 线程里尤其重要：清理失败不该让整个任务无声地死掉。
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def write_via_temp(target: str | Path, write: Callable[[Path], None]) -> Path:
    """写临时文件后替换到目标；失败时清掉临时文件并原样抛错。

    ``write`` 收到的是临时文件路径，往里写就行（不要自己去动 target）。
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_path_for(target)
    try:
        write(temp)
        return publish(temp, target)
    except BaseException:
        discard(temp)
        raise


def timestamped_path(target: str | Path, *, now: datetime | None = None) -> Path:
    """原名 + 时间戳的备选名：``报告.xlsx`` → ``报告_20261009_153012.xlsx``。

    同名已存在时继续加 ``_2`` / ``_3``，保证返回一个当前不存在的路径。
    """
    target = Path(target)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    candidate = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
    index = 2
    while candidate.exists():
        candidate = target.with_name(
            f"{target.stem}_{stamp}_{index}{target.suffix}")
        index += 1
    return candidate

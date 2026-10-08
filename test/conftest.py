"""test/ 下的共用夹具。

这里放两样东西：

1. ``locker``：**真实的**文件占用。Excel / WPS 持有表格时的语义（别人打不开、
   写不进、也替换不掉）没法用 mock 模拟，所以用 ctypes 以 ``dwShareMode=0``
   打开目标文件，得到同样的拒绝写 / 拒绝删除行为。
2. ``work_dir``：可读写的临时目录（不用 ``tmp_path``，理由见 test_scraper.py）。
"""

from __future__ import annotations

import ctypes
import shutil
import sys
import tempfile
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest

_IS_WINDOWS = sys.platform == "win32"
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

if _IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]


class FileLocker:
    """独占占用文件；用完（或夹具结束）自动释放。"""

    def __init__(self) -> None:
        self._handles: list[int] = []

    def lock(self, path: str | Path) -> FileLocker:
        if not _IS_WINDOWS:
            pytest.skip("文件占用语义只在 Windows 上验证")
        handle = _k32.CreateFileW(
            str(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, 0, None)
        if handle == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handles.append(handle)
        return self

    def release(self) -> None:
        while self._handles:
            _k32.CloseHandle(self._handles.pop())


@pytest.fixture
def locker():
    obj = FileLocker()
    try:
        yield obj
    finally:
        obj.release()


@pytest.fixture
def work_dir():
    base = Path(tempfile.gettempdir()) / f"javscraper_test_{uuid.uuid4().hex}"
    base.mkdir()
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)

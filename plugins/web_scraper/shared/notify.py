"""爬取结束时的提示：声音 / Windows 系统通知。

不引入新依赖：
- 声音用标准库 winsound（仅 Windows 有效）
- 通知用 PowerShell 调用 System.Windows.Forms.NotifyIcon
  （Popen 异步执行，不阻塞 UI）
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"


def play_beep() -> None:
    """播放系统提示音。非 Windows 静默跳过。"""
    if not _IS_WINDOWS:
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        logger.debug("播放提示音失败", exc_info=True)


def show_toast(title: str, message: str, duration_ms: int = 8000) -> None:
    """弹出 Windows 系统通知。

    用 PowerShell 调 NotifyIcon.ShowBalloonTip 生成通知，
    Windows 10/11 会把它转成现代通知中心里的一条。

    非 Windows 或 PowerShell 调用失败时静默返回。
    """
    if not _IS_WINDOWS:
        return

    # 简单转义：单引号在 PowerShell 里用两个单引号表示
    def _q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    ps_script = (
        "[reflection.assembly]::loadwithpartialname('System.Windows.Forms')"
        " | Out-Null;"
        "[reflection.assembly]::loadwithpartialname('System.Drawing')"
        " | Out-Null;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip({duration_ms}, {_q(title)}, {_q(message)}, "
        "[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Milliseconds 300;"
        "$n.Dispose();"
    )

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("弹出系统通知失败", exc_info=True)
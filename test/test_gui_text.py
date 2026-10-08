"""GUI 文案函数的单元测试（不创建窗口）。

这些函数只拼字符串，不需要 Tk，所以能直接测。它们承担两件容易被忽略的事：

  - 预览阶段的重名提醒必须讲清后果（「覆盖」会丢文件、且找不回来）
  - 移动 / 撤回失败时，日志里要说清是哪个文件、为什么

对应的调用点见 gui/move_tab.py。
"""

from __future__ import annotations

from pathlib import Path

from av_scraper.gui.move_tab import (
    build_collision_message,
    move_status_text,
    undo_status_text,
)
from av_scraper.processor import MoveOperation, PlannedOperation


def plan_op(src: str, dst: str, status: str = "move") -> PlannedOperation:
    return PlannedOperation(
        src=Path(src), dst=Path(dst), status=status, extracted_code="ABP-123"
    )


def move_op(original: str, moved_to: str) -> MoveOperation:
    return MoveOperation(
        original_path=original,
        moved_to=moved_to,
        original_filename=Path(original).name,
        target_filename=Path(moved_to).name,
        extracted_code="ABP-123",
    )


# ---------------------------------------------------------------------------
# 预览重名提醒
# ---------------------------------------------------------------------------
def test_collision_message_states_the_consequence():
    dst = r"D:\dst\ABP-123\ABP-123.mp4"
    collisions = {
        dst: [
            plan_op(r"C:\a\ABP-123.mp4", dst),
            plan_op(r"C:\b\ABP-123.mp4", dst, status="overwrite"),
        ]
    }
    msg = build_collision_message(collisions)

    assert "2 个文件" in msg and "1 处" in msg
    assert "覆盖" in msg and "无法找回" in msg        # 说清后果
    assert "重命名" in msg                            # 给出建议
    assert "a/ABP-123.mp4" in msg                     # 同名文件要能区分开
    assert "b/ABP-123.mp4" in msg


def test_collision_message_truncates_long_lists():
    collisions = {
        f"D:/dst/ABP-{i}/ABP-{i}.mp4": [
            plan_op(f"D:/src/a{i}/ABP-{i}.mp4", f"D:/dst/ABP-{i}/ABP-{i}.mp4"),
            plan_op(f"D:/src/b{i}/ABP-{i}.mp4", f"D:/dst/ABP-{i}/ABP-{i}.mp4", "overwrite"),
        ]
        for i in range(7)
    }
    msg = build_collision_message(collisions)

    assert "共 7 处" in msg and "14 个文件" in msg
    assert "另有 2 处冲突未列出" in msg               # 只列前 5 处


# ---------------------------------------------------------------------------
# 移动过程的日志文案
# ---------------------------------------------------------------------------
def test_move_status_text_is_silent_on_success():
    op = plan_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")
    assert move_status_text(op, "ok") == ""


def test_move_status_text_explains_skip_and_missing():
    op = plan_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")

    assert "目标已存在" in move_status_text(op, "skip")
    assert "源文件不存在" in move_status_text(op, "missing")


def test_move_status_text_keeps_os_error_message():
    op = plan_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")
    text = move_status_text(op, "error:[WinError 5] 拒绝访问。")

    assert "失败" in text
    assert "ABP-123.mp4" in text                      # 哪个文件
    assert "[WinError 5] 拒绝访问。" in text           # 系统给的原因要保留


# ---------------------------------------------------------------------------
# 撤回过程的日志文案
# ---------------------------------------------------------------------------
def test_undo_status_text_is_silent_on_success():
    op = move_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")
    assert undo_status_text(op, "ok") == ""


def test_undo_status_text_explains_missing():
    op = move_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")
    text = undo_status_text(op, "missing")

    assert "失败" in text and "ABP-123.mp4" in text
    assert "找不到该文件" in text


def test_undo_status_text_explains_occupied():
    op = move_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")
    text = undo_status_text(op, "occupied")

    assert "失败" in text
    assert "原路径已有同名文件" in text
    assert "已跳过以免覆盖" in text
    assert r"C:\src\ABP-123.mp4" in text               # 指出是哪个位置被占用


def test_undo_status_text_keeps_os_error_message():
    op = move_op(r"C:\src\ABP-123.mp4", r"D:\dst\ABP-123\ABP-123.mp4")
    text = undo_status_text(op, "error:[WinError 32] 另一个程序正在使用此文件")

    assert "失败" in text
    assert "另一个程序正在使用此文件" in text

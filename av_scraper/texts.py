"""窗口与命令行共用的文案。

这些函数只拼字符串、不依赖 tkinter，所以 GUI（``gui/move_tab.py``）和命令行
（``cli.py``）说的是同一句话——同一件事在窗口里和终端里不该有两种说法。

只有"补救方式"必须区分：窗口里是"勾选"，终端里是 ``--only``。
"""

from __future__ import annotations

from pathlib import Path

from .processor import MoveOperation, PlannedOperation

# 「批内重名」提醒的结尾。GUI 与 CLI 的补救方式不同，所以整段跟着换。
COLLISION_TAIL_GUI = (
    "如果「配置 → 文件冲突处理」当前是「覆盖」，"
    "先移入的那个文件会被后一个覆盖删除，且无法找回。\n"
    "建议：把冲突处理改成「重命名」，或者只勾选其中一个文件再执行。"
)

COLLISION_TAIL_CLI = (
    "如果当前设置是「覆盖」，先移入的那个文件会被后一个覆盖删除，且无法找回。\n"
    "建议：改用 --on-conflict rename，或者用 --only 只处理其中一个文件。"
)


def short_path(path: Path) -> str:
    """文件在提醒里的短标识：上级目录名/文件名，便于区分同名文件。"""
    return f"{path.parent.name}/{path.name}" if path.parent.name else path.name


def build_collision_message(
    collisions: dict[str, list[PlannedOperation]],
    *,
    tail: str = COLLISION_TAIL_GUI,
) -> str:
    """预览阶段"批内重名"提醒的文案。"""
    groups = list(collisions.items())
    shown, rest = groups[:5], groups[5:]
    lines = [
        f"目标：{Path(dst).name}\n   来自：" + "、".join(short_path(p.src) for p in ops)
        for dst, ops in shown
    ]
    if rest:
        lines.append(f"……另有 {len(rest)} 处冲突未列出")

    files = sum(len(ops) for ops in collisions.values())
    return (
        f"检测到本批有 {files} 个文件会写到同样的目标路径（共 {len(groups)} 处）：\n\n"
        + "\n".join(lines)
        + "\n\n"
        + tail
    )


_MOVE_SKIP_TEXT = {
    "skip": "跳过（目标已存在）",
    "missing": "跳过（源文件不存在）",
}


def move_status_text(op: PlannedOperation, status: str) -> str:
    """移动过程中值得写进日志的一行；正常完成返回空串。"""
    if status == "ok":
        return ""
    if status.startswith("error:"):
        return f"[失败] {op.src.name} → {op.dst}：{status[len('error:'):]}"
    return f"[{_MOVE_SKIP_TEXT.get(status, status)}] {op.src.name}"


def undo_status_text(op: MoveOperation, status: str) -> str:
    """撤回过程中值得写进日志的一行；正常完成返回空串。"""
    if status == "ok":
        return ""
    if status == "already":
        return f"[跳过] {op.original_filename}：这个文件已经撤回过了"
    if status == "missing":
        return f"[失败] {op.original_filename}：目标位置已找不到该文件，无法撤回"
    if status == "occupied":
        return (f"[失败] {op.original_filename}：原路径已有同名文件，"
                f"已跳过以免覆盖（{op.original_path}）")
    if status.startswith("error:"):
        return f"[失败] {op.original_filename}：{status[len('error:'):]}"
    return f"[失败] {op.original_filename}：{status}"

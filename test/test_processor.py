"""av_scraper.processor 的单元测试。

覆盖四块：
  1. load_results  —— 读扫描结果 JSON
  2. plan          —— 目标路径规划、冲突处理、"跟随视频命名"
  3. execute       —— 实际移动 / 覆盖 / 跳过 / 失败统计与进度回调
  4. 记录与撤回     —— save_operations / load_operations / undo

临时目录的说明同 test_scraper.py：不用 pytest 的 tmp_path（它以 0o700 建目录，
在部分受限环境下连列目录都会被拒），改用默认权限自建。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from av_scraper.config import ProcessorConfig
from av_scraper.processor import FileProcessor, MoveOperation, PlannedOperation


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------
@pytest.fixture
def dirs():
    """返回 (src, dst, base)：源目录、目标目录、两者的父目录（退出时整个清掉）。"""
    base = Path(tempfile.gettempdir()) / f"javscraper_proc_{uuid.uuid4().hex}"
    src = base / "src"
    dst = base / "dst"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    try:
        yield src, dst, base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def make_processor(target: Path, **overrides) -> FileProcessor:
    values = dict(
        target_directory=str(target),
        move_to_extracted_folder=True,
        video_naming="保留原文件名",
        attachment_naming="保留原文件名",
        existing_file_handling="重命名",
    )
    values.update(overrides)
    return FileProcessor(
        ProcessorConfig(**values),
        video_exts=[".mp4", ".mkv"],
        attachment_exts=[".srt"],
    )


def make_file(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def result(path: Path, code: str, status: str = "extracted") -> dict:
    """构造与 reports.save_json 相同结构的扫描结果条目。"""
    return {
        "file_path": str(path),
        "filename": path.name,
        "extracted_code": code,
        "status": status,
        "file_size": path.stat().st_size if path.exists() else 0,
        "inherited": False,
        "manually_edited": False,
    }


# ---------------------------------------------------------------------------
# 1. load_results
# ---------------------------------------------------------------------------
def test_load_results_reads_json(dirs):
    _, _, base = dirs
    data = [{"file_path": "a.mp4", "status": "extracted", "extracted_code": "ABP-123"}]
    path = base / "results.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert FileProcessor.load_results(path) == data


def test_load_results_missing_file_raises(dirs):
    _, _, base = dirs
    with pytest.raises(FileNotFoundError):
        FileProcessor.load_results(base / "nope.json")


# ---------------------------------------------------------------------------
# 2. plan：目标路径
# ---------------------------------------------------------------------------
def test_plan_ignores_non_extracted(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4")
    b = make_file(src / "random.mp4")
    planned = make_processor(dst).plan(
        [result(a, "ABP-123"), result(b, b.name, status="original")]
    )
    assert [p.src for p in planned] == [a]


def test_plan_does_not_touch_files(dirs):
    src, dst, _ = dirs
    a = make_file(src / "some title.mp4")
    proc = make_processor(dst)
    planned = proc.plan([result(a, "ABP-123")])
    assert a.exists() and not planned[0].dst.exists()      # 规划阶段只算路径，不动文件


def test_plan_puts_file_in_code_folder(dirs):
    src, dst, _ = dirs
    a = make_file(src / "some title.mp4")
    (op,) = make_processor(dst).plan([result(a, "ABP-123")])
    assert op.dst == dst / "ABP-123" / "some title.mp4"
    assert op.status == "move"
    assert op.extracted_code == "ABP-123"


def test_plan_flat_layout_when_not_using_code_folder(dirs):
    src, dst, _ = dirs
    a = make_file(src / "some title.mp4")
    (op,) = make_processor(dst, move_to_extracted_folder=False).plan([result(a, "ABP-123")])
    assert op.dst == dst / "some title.mp4"


def test_plan_video_naming_by_code(dirs):
    src, dst, _ = dirs
    a = make_file(src / "乱七八糟的名字.mp4")
    (op,) = make_processor(dst, video_naming="按提取码命名").plan([result(a, "ABP-123")])
    assert op.dst == dst / "ABP-123" / "ABP-123.mp4"


def test_plan_attachment_naming_by_code(dirs):
    src, dst, _ = dirs
    s = make_file(src / "中文字幕.srt")
    (op,) = make_processor(dst, attachment_naming="按提取码命名").plan([result(s, "ABP-123")])
    assert op.dst == dst / "ABP-123" / "ABP-123.srt"


def test_plan_attachment_keeps_name_by_default(dirs):
    src, dst, _ = dirs
    s = make_file(src / "中文字幕.srt")
    (op,) = make_processor(dst).plan([result(s, "ABP-123")])
    assert op.dst == dst / "ABP-123" / "中文字幕.srt"


# ---------------------------------------------------------------------------
# 3. plan：目标已存在 / 批内重名
# ---------------------------------------------------------------------------
def test_plan_rename_when_target_exists(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4")
    make_file(dst / "ABP-123" / "ABP-123.mp4")
    (op,) = make_processor(dst).plan([result(a, "ABP-123")])
    assert op.status == "rename"
    assert op.dst == dst / "ABP-123" / "ABP-123_01.mp4"


def test_plan_rename_skips_taken_suffixes(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4")
    make_file(dst / "ABP-123" / "ABP-123.mp4")
    make_file(dst / "ABP-123" / "ABP-123_01.mp4")
    (op,) = make_processor(dst).plan([result(a, "ABP-123")])
    assert op.dst == dst / "ABP-123" / "ABP-123_02.mp4"


def test_plan_skip_when_target_exists(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4", "new")
    existing = make_file(dst / "ABP-123" / "ABP-123.mp4", "old")
    (op,) = make_processor(dst, existing_file_handling="跳过").plan([result(a, "ABP-123")])
    assert op.status == "skip"
    assert op.dst == dst / "ABP-123" / "ABP-123.mp4"
    assert existing.read_text(encoding="utf-8") == "old"
    assert a.read_text(encoding="utf-8") == "new"


def test_plan_overwrite_when_target_exists(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4", "new")
    make_file(dst / "ABP-123" / "ABP-123.mp4", "old")
    (op,) = make_processor(dst, existing_file_handling="覆盖").plan([result(a, "ABP-123")])
    assert op.status == "overwrite"
    assert op.dst == dst / "ABP-123" / "ABP-123.mp4"


def test_plan_duplicate_targets_in_batch_get_suffixed(dirs):
    """同一批里两个文件算到同一个目标名时，第二个自动加后缀。"""
    src, dst, _ = dirs
    a = make_file(src / "a" / "ABP-123.mp4")
    b = make_file(src / "b" / "ABP-123.mp4")
    planned = make_processor(dst).plan([result(a, "ABP-123"), result(b, "ABP-123")])
    assert [p.status for p in planned] == ["move", "rename"]
    assert planned[0].dst == dst / "ABP-123" / "ABP-123.mp4"
    assert planned[1].dst == dst / "ABP-123" / "ABP-123_01.mp4"


def test_plan_duplicate_targets_in_batch_skipped(dirs):
    src, dst, _ = dirs
    a = make_file(src / "a" / "ABP-123.mp4")
    b = make_file(src / "b" / "ABP-123.mp4")
    planned = make_processor(dst, existing_file_handling="跳过").plan(
        [result(a, "ABP-123"), result(b, "ABP-123")]
    )
    assert [p.status for p in planned] == ["move", "skip"]


def test_plan_duplicate_targets_in_batch_overwrite_is_lossy(dirs):
    """现状记录（有数据丢失风险，见提交说明）：

    一批里目标重名 + 冲突处理选"覆盖"时，两个文件都指向同一个目标；
    execute 先移入第一个，再删掉它换第二个 —— 第一个文件就此消失。
    """
    src, dst, _ = dirs
    a = make_file(src / "a" / "ABP-123.mp4", "first")
    b = make_file(src / "b" / "ABP-123.mp4", "second")
    proc = make_processor(dst, existing_file_handling="覆盖")
    planned = proc.plan([result(a, "ABP-123"), result(b, "ABP-123")])
    assert [p.status for p in planned] == ["move", "overwrite"]

    ops, success, skipped, failed = proc.execute(planned)
    assert (success, skipped, failed) == (2, 0, 0)
    assert (dst / "ABP-123" / "ABP-123.mp4").read_text(encoding="utf-8") == "second"
    assert len(list((dst / "ABP-123").iterdir())) == 1     # 第一个文件确实没了


# ---------------------------------------------------------------------------
# 4. plan：跟随视频命名
# ---------------------------------------------------------------------------
def test_plan_follow_video_renames_attachment(dirs):
    src, dst, _ = dirs
    v = make_file(src / "ABP-123.mp4")
    s = make_file(src / "中文字幕.srt")
    planned = make_processor(dst, attachment_naming="跟随视频命名").plan(
        [result(v, "ABP-123"), result(s, "ABP-123")]
    )
    targets = {p.src.name: p.dst for p in planned}
    assert targets["ABP-123.mp4"] == dst / "ABP-123" / "ABP-123.mp4"
    assert targets["中文字幕.srt"] == dst / "ABP-123" / "ABP-123.srt"


def test_plan_follow_video_with_code_naming(dirs):
    src, dst, _ = dirs
    v = make_file(src / "random name.mp4")
    s = make_file(src / "sub.srt")
    planned = make_processor(
        dst, attachment_naming="跟随视频命名", video_naming="按提取码命名",
    ).plan([result(v, "ABP-123"), result(s, "ABP-123")])
    targets = {p.src.name: p.dst for p in planned}
    assert targets["random name.mp4"] == dst / "ABP-123" / "ABP-123.mp4"
    assert targets["sub.srt"] == dst / "ABP-123" / "ABP-123.srt"


def test_plan_follow_video_falls_back_without_video(dirs):
    """同目录没有视频时，附件退回普通命名规则。"""
    src, dst, _ = dirs
    s = make_file(src / "sub.srt")
    (op,) = make_processor(dst, attachment_naming="跟随视频命名").plan([result(s, "ABP-123")])
    assert op.dst == dst / "ABP-123" / "sub.srt"


def test_plan_follow_video_falls_back_with_multiple_videos(dirs):
    """同目录多于一个视频时不配对，附件同样退回。"""
    src, dst, _ = dirs
    v1 = make_file(src / "ABP-123.mp4")
    v2 = make_file(src / "ABP-124.mp4")
    s = make_file(src / "sub.srt")
    planned = make_processor(dst, attachment_naming="跟随视频命名").plan(
        [result(v1, "ABP-123"), result(v2, "ABP-124"), result(s, "ABP-123")]
    )
    targets = {p.src.name: p.dst for p in planned}
    assert targets["ABP-123.mp4"] == dst / "ABP-123" / "ABP-123.mp4"
    assert targets["ABP-124.mp4"] == dst / "ABP-124" / "ABP-124.mp4"
    assert targets["sub.srt"] == dst / "ABP-123" / "sub.srt"


def test_plan_follow_video_does_not_cross_directories(dirs):
    src, dst, _ = dirs
    v = make_file(src / "a" / "ABP-123.mp4")
    s = make_file(src / "b" / "sub.srt")
    planned = make_processor(dst, attachment_naming="跟随视频命名").plan(
        [result(v, "ABP-123"), result(s, "ABP-123")]
    )
    targets = {p.src.name: p.dst for p in planned}
    assert targets["sub.srt"] == dst / "ABP-123" / "sub.srt"   # 没被 a/ 里的视频带走


# ---------------------------------------------------------------------------
# 5. execute
# ---------------------------------------------------------------------------
def test_execute_moves_files_and_records_operations(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4", "aaa")
    b = make_file(src / "SSIS-456.mp4", "bbb")
    proc = make_processor(dst)
    ops, success, skipped, failed = proc.execute(
        proc.plan([result(a, "ABP-123"), result(b, "SSIS-456")])
    )

    assert (success, skipped, failed) == (2, 0, 0)
    assert not a.exists() and not b.exists()
    assert (dst / "ABP-123" / "ABP-123.mp4").read_text(encoding="utf-8") == "aaa"
    assert (dst / "SSIS-456" / "SSIS-456.mp4").read_text(encoding="utf-8") == "bbb"

    assert [o.original_filename for o in ops] == ["ABP-123.mp4", "SSIS-456.mp4"]
    assert [o.target_filename for o in ops] == ["ABP-123.mp4", "SSIS-456.mp4"]
    assert [o.extracted_code for o in ops] == ["ABP-123", "SSIS-456"]
    assert ops[0].original_path == str(a)
    assert ops[0].moved_to == str(dst / "ABP-123" / "ABP-123.mp4")


def test_execute_creates_missing_target_directories(dirs):
    src, dst, base = dirs
    a = make_file(src / "ABP-123.mp4")
    target = base / "not" / "created" / "yet"
    proc = make_processor(target)
    proc.execute(proc.plan([result(a, "ABP-123")]))
    assert (target / "ABP-123" / "ABP-123.mp4").exists()


def test_execute_skips_skip_status(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4", "new")
    make_file(dst / "ABP-123" / "ABP-123.mp4", "old")
    proc = make_processor(dst, existing_file_handling="跳过")
    seen: list[str] = []
    ops, success, skipped, failed = proc.execute(
        proc.plan([result(a, "ABP-123")]),
        progress=lambda i, t, op, s: seen.append(s),
    )

    assert (success, skipped, failed) == (0, 1, 0)
    assert ops == []
    assert seen == ["skip"]
    assert a.exists()                                       # 源文件原封不动
    assert (dst / "ABP-123" / "ABP-123.mp4").read_text(encoding="utf-8") == "old"


def test_execute_counts_missing_source_as_failed(dirs):
    src, dst, _ = dirs
    ghost = src / "gone.mp4"                                # 源文件已不在
    planned = [PlannedOperation(
        src=ghost, dst=dst / "ABP-123" / "gone.mp4",
        status="move", extracted_code="ABP-123",
    )]
    proc = make_processor(dst)
    seen: list[str] = []
    ops, success, skipped, failed = proc.execute(
        planned, progress=lambda i, t, op, s: seen.append(s)
    )

    assert (success, skipped, failed) == (0, 0, 1)
    assert seen == ["missing"]


def test_execute_overwrite_replaces_existing_file(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4", "new")
    make_file(dst / "ABP-123" / "ABP-123.mp4", "old")
    proc = make_processor(dst, existing_file_handling="覆盖")
    ops, success, skipped, failed = proc.execute(proc.plan([result(a, "ABP-123")]))

    assert (success, skipped, failed) == (1, 0, 0)
    assert (dst / "ABP-123" / "ABP-123.mp4").read_text(encoding="utf-8") == "new"
    assert len(list((dst / "ABP-123").iterdir())) == 1


def test_execute_reports_error_when_target_directory_is_blocked(dirs):
    """目标位置被同名文件占住：计入失败，源文件不受影响。"""
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4", "keep me")
    make_file(dst / "ABP-123")                              # 挡住目录位置
    proc = make_processor(dst)
    seen: list[str] = []
    ops, success, skipped, failed = proc.execute(
        proc.plan([result(a, "ABP-123")]),
        progress=lambda i, t, op, s: seen.append(s),
    )

    assert (success, skipped, failed) == (0, 0, 1)
    assert len(seen) == 1 and seen[0].startswith("error:")
    assert a.read_text(encoding="utf-8") == "keep me"


def test_execute_progress_sequence(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4")
    b = make_file(src / "ABP-124.mp4")
    make_file(dst / "ABP-124" / "ABP-124.mp4")              # b 冲突 → skip
    proc = make_processor(dst, existing_file_handling="跳过")
    seen: list[tuple[int, int, str]] = []
    proc.execute(
        proc.plan([result(a, "ABP-123"), result(b, "ABP-124")]),
        progress=lambda i, t, op, s: seen.append((i, t, s)),
    )
    assert seen == [(1, 2, "ok"), (2, 2, "skip")]


def test_execute_empty_plan(dirs):
    _, dst, _ = dirs
    assert make_processor(dst).execute([]) == ([], 0, 0, 0)


def test_execute_extracted_code_is_empty_for_flat_layout(dirs):
    src, dst, _ = dirs
    a = make_file(src / "ABP-123.mp4")
    proc = make_processor(dst, move_to_extracted_folder=False)
    ops, *_ = proc.execute(proc.plan([result(a, "ABP-123")]))
    assert ops[0].extracted_code == ""


# ---------------------------------------------------------------------------
# 6. 记录与撤回
# ---------------------------------------------------------------------------
def test_save_and_load_operations_round_trip(dirs):
    _, _, base = dirs
    ops = [MoveOperation(
        original_path=r"C:\src\中文字幕.srt",
        moved_to=r"D:\dst\ABP-123\ABP-123.srt",
        original_filename="中文字幕.srt",
        target_filename="ABP-123.srt",
        extracted_code="ABP-123",
    )]
    path = base / "sub" / "ops.json"                        # 父目录会被建出来
    FileProcessor.save_operations(ops, path)

    assert FileProcessor.load_operations(path) == ops
    assert "中文字幕.srt" in path.read_text(encoding="utf-8")   # 中文不被转义


def test_undo_restores_files_and_removes_record(dirs):
    src, dst, base = dirs
    a = make_file(src / "sub" / "ABP-123.mp4", "aaa")
    b = make_file(src / "SSIS-456.mp4", "bbb")
    proc = make_processor(dst)
    ops, *_ = proc.execute(proc.plan([result(a, "ABP-123"), result(b, "SSIS-456")]))
    ops_file = base / "ops.json"
    proc.save_operations(ops, ops_file)

    success, failed = proc.undo(ops_file)

    assert (success, failed) == (2, 0)
    assert a.read_text(encoding="utf-8") == "aaa"
    assert b.read_text(encoding="utf-8") == "bbb"
    assert not (dst / "ABP-123" / "ABP-123.mp4").exists()
    assert not ops_file.exists()                            # 全部成功 → 记录被删


def test_undo_recreates_original_directory(dirs):
    src, dst, base = dirs
    a = make_file(src / "sub" / "ABP-123.mp4", "aaa")
    proc = make_processor(dst)
    ops, *_ = proc.execute(proc.plan([result(a, "ABP-123")]))
    ops_file = base / "ops.json"
    proc.save_operations(ops, ops_file)
    shutil.rmtree(src / "sub")                              # 原目录已被删掉

    assert proc.undo(ops_file) == (1, 0)
    assert a.read_text(encoding="utf-8") == "aaa"


def test_undo_keeps_record_when_something_is_missing(dirs):
    src, dst, base = dirs
    a = make_file(src / "ABP-123.mp4", "aaa")
    b = make_file(src / "SSIS-456.mp4", "bbb")
    proc = make_processor(dst)
    ops, *_ = proc.execute(proc.plan([result(a, "ABP-123"), result(b, "SSIS-456")]))
    ops_file = base / "ops.json"
    proc.save_operations(ops, ops_file)
    (dst / "ABP-123" / "ABP-123.mp4").unlink()              # 有一个已不在目标位置

    seen: list[str] = []
    success, failed = proc.undo(ops_file, progress=lambda op, s: seen.append(s))

    assert (success, failed) == (1, 1)
    assert "missing" in seen
    assert ops_file.exists()                                # 有失败 → 保留记录可重试
    assert b.exists() and not (dst / "SSIS-456" / "SSIS-456.mp4").exists()


def test_undo_missing_record_raises(dirs):
    _, dst, base = dirs
    with pytest.raises(FileNotFoundError):
        make_processor(dst).undo(base / "no-such-ops.json")


def test_undo_empty_record_keeps_file(dirs):
    _, dst, base = dirs
    ops_file = base / "ops.json"
    FileProcessor.save_operations([], ops_file)
    assert make_processor(dst).undo(ops_file) == (0, 0)
    assert ops_file.exists()                                # 什么都没做 → 不删记录


def test_undo_overwrites_file_that_reappeared(dirs):
    """现状记录：撤回时不检查原路径是否已被占用，直接覆盖。"""
    src, dst, base = dirs
    a = make_file(src / "ABP-123.mp4", "moved content")
    proc = make_processor(dst)
    ops, *_ = proc.execute(proc.plan([result(a, "ABP-123")]))
    ops_file = base / "ops.json"
    proc.save_operations(ops, ops_file)
    make_file(a, "someone else wrote here")                 # 原路径又出现同名文件

    assert proc.undo(ops_file) == (1, 0)
    assert a.read_text(encoding="utf-8") == "moved content"

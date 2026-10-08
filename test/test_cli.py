"""命令行模式的单元测试。

这些用例真的建文件、真的移文件——命令行是"会动用户文件"的入口，
只用 mock 验参数解析没有意义。临时目录用 conftest 的 ``work_dir``（在 %TEMP% 下）。

覆盖的重点：

  - 扫描导出走的是和图形界面同一套目录/文件名约定
  - **危险动作的安全阀**：不动文件的默认预览、非交互环境必须显式 --yes
  - 与图形界面共用同一份移动记录，撤回可重复执行（被打断也能接着撤）
  - 输出只用 GBK 能编码的字符（Windows 中文控制台是 cp936，
    ``✓`` ``✗`` ``↳`` ``⚠`` 会直接抛 UnicodeEncodeError）

每个用例都显式传 ``-c``，绝不读写仓库里那份真实的 config.json / log/。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from av_scraper import cli
from av_scraper.cli import Console, display_width, main, pad_display
from av_scraper.processor import FileProcessor, MoveOperation


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------
@dataclass
class CliResult:
    code: int
    out: str
    err: str
    data: str
    prompts: list[str] = field(default_factory=list)


class FakeAsk:
    """假的 input()：按顺序吐答案，同时记下问过什么。"""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "n"


def run_cli(argv, *, interactive: bool = False, answers=()) -> CliResult:
    """跑一次命令行，把三个输出流和提问都收下来。"""
    out, err, data = io.StringIO(), io.StringIO(), io.StringIO()
    ask = FakeAsk(answers)
    ui = Console(out=out, err=err, data_out=data, ask=ask, interactive=interactive)
    code = main(list(argv), console=ui)
    return CliResult(code, out.getvalue(), err.getvalue(), data.getvalue(), ask.prompts)


def config_args(work_dir: Path) -> list[str]:
    """所有用例都要显式指定配置文件，免得碰到仓库里那份真的。"""
    return ["-c", str(work_dir / "config.json")]


def write_config(work_dir: Path, data: dict) -> Path:
    path = work_dir / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def entry(file_path: Path, code: str, status: str = "extracted") -> dict:
    return {
        "file_path": str(file_path),
        "filename": file_path.name,
        "extracted_code": code,
        "status": status,
        "file_size": file_path.stat().st_size if file_path.exists() else 0,
        "inherited": False,
        "manually_edited": False,
    }


def write_results(path: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


def make_file(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def only_dir(base: Path) -> Path:
    """输出根目录下唯一的时间戳运行目录。"""
    dirs = [p for p in base.iterdir() if p.is_dir()]
    assert len(dirs) == 1, f"期望只有一个运行目录，实际 {dirs}"
    return dirs[0]


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def test_scan_writes_three_reports_into_timestamped_dir(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    make_file(src / "SSIS-001.mkv")
    make_file(src / "random.mkv")            # 认不出番号，但仍是受支持的扩展名
    out = work_dir / "out"

    res = run_cli(["scan", str(src), "-o", str(out), *config_args(work_dir)])

    assert res.code == 0
    run_dir = only_dir(out)
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "extraction_report.txt", "extraction_table.csv", "scraper_results.json",
    ]
    saved = json.loads((run_dir / "scraper_results.json").read_text(encoding="utf-8"))
    assert len(saved) == 3
    assert {e["extracted_code"] for e in saved if e["status"] == "extracted"} == {
        "ABP-123", "SSIS-001",
    }
    assert "文件总数" in res.out and "提取率" in res.out
    assert "已保存" in res.out
    assert "random.mkv" in res.out                # 未识别的要列出来
    assert res.err == ""


def test_scan_flat_skips_the_timestamp_dir(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    out = work_dir / "out"

    res = run_cli(["scan", str(src), "-o", str(out), "--flat", *config_args(work_dir)])

    assert res.code == 0
    assert (out / "scraper_results.json").exists()
    assert (out / "extraction_report.txt").exists()


def test_scan_json_flag_puts_reports_next_to_that_file(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    target = work_dir / "elsewhere" / "my_results.json"

    res = run_cli(["scan", str(src), "--json", str(target), *config_args(work_dir)])

    assert res.code == 0
    assert target.exists()
    # 不再另建时间戳目录，另外两份报告跟在 JSON 旁边
    assert (target.parent / "extraction_report.txt").exists()
    assert (target.parent / "extraction_table.csv").exists()


def test_scan_no_reports_writes_nothing(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    out = work_dir / "out"

    res = run_cli(["scan", str(src), "-o", str(out), "--no-reports", *config_args(work_dir)])

    assert res.code == 0
    assert not out.exists()                       # 连目录都不建
    assert "文件总数" in res.out


def test_scan_stdout_json_is_pure_json(work_dir):
    """--json - 时标准输出只留数据，摘要走 stderr（管道里能直接喂给别的工具）。"""
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    out, err = io.StringIO(), io.StringIO()

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["scan", str(src), "--json", "-", *config_args(work_dir)])

    assert code == 0
    payload = json.loads(out.getvalue())          # 必须能整段解析
    assert payload[0]["extracted_code"] == "ABP-123"
    assert "文件总数" in err.getvalue()            # 摘要跑到 stderr 去了
    assert "文件总数" not in out.getvalue()


def test_scan_summary_truncates_unrecognized_list(work_dir):
    src = work_dir / "src"
    for name in ("a.mkv", "b.mkv", "c.mkv"):
        make_file(src / name)

    res = run_cli(["scan", str(src), "--no-reports", "--limit", "2",
                   *config_args(work_dir)])

    assert "未识别（3）" in res.out
    assert "……另有 1 个未列出" in res.out


def test_scan_limit_zero_lists_everything(work_dir):
    src = work_dir / "src"
    for name in ("a.mkv", "b.mkv", "c.mkv"):
        make_file(src / name)

    res = run_cli(["scan", str(src), "--no-reports", "--limit", "0",
                   *config_args(work_dir)])

    assert "未列出" not in res.out
    assert res.out.count(".mkv") == 3


def test_scan_verbose_lists_the_extracted_codes(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")

    res = run_cli(["scan", str(src), "--no-reports", "-v", *config_args(work_dir)])

    assert "明细：" in res.out
    assert "[提取] ABP-123" in res.out


def test_scan_recursive_flag_overrides_config(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    make_file(src / "sub" / "SSIS-001.mkv")
    write_config(work_dir, {"scraper": {"recursive_processing": False}})

    flat = run_cli(["scan", str(src), "--no-reports", *config_args(work_dir)])
    deep = run_cli(["scan", str(src), "--no-reports", "--recursive",
                    *config_args(work_dir)])

    assert "文件总数    1" in flat.out
    assert "文件总数    2" in deep.out


def test_scan_reports_bad_config_value_on_stderr(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    write_config(work_dir, {"processor": {"existing_file_handling": "乱写的值"}})

    res = run_cli(["scan", str(src), "--no-reports", *config_args(work_dir)])

    assert res.code == 0
    assert "不是有效选项" in res.err


def test_scan_missing_directory_exits_1(work_dir):
    res = run_cli(["scan", str(work_dir / "没有这个目录"), *config_args(work_dir)])

    assert res.code == 1
    assert "目录不存在" in res.err
    assert res.out == ""


def test_scan_quiet_prints_nothing_on_success(work_dir):
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")

    res = run_cli(["scan", str(src), "--no-reports", "-q", *config_args(work_dir)])

    assert res.code == 0
    assert res.out == ""
    assert res.err == ""


# ---------------------------------------------------------------------------
# 移动：先看再动
# ---------------------------------------------------------------------------
def test_move_without_target_exits_1(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])

    res = run_cli(["move", str(results), *config_args(work_dir)])

    assert res.code == 1
    assert "没有目标目录" in res.err
    assert src.exists()


def test_move_dry_run_touches_nothing(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])
    target = work_dir / "dst"

    res = run_cli(["move", str(results), "-t", str(target), "--dry-run",
                   *config_args(work_dir)])

    assert res.code == 0
    assert "预览结束" in res.out
    assert "本次将处理 1 个文件" in res.out
    assert src.exists()
    assert not target.exists()


def test_move_refuses_without_yes_in_non_interactive(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])

    res = run_cli(["move", str(results), "-t", str(work_dir / "dst"),
                   *config_args(work_dir)])

    assert res.code == 2
    assert "必须加 --yes" in res.err
    assert src.exists()


def test_move_declining_the_prompt_changes_nothing(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])

    res = run_cli(["move", str(results), "-t", str(work_dir / "dst"),
                   *config_args(work_dir)],
                  interactive=True, answers=["n"])

    assert res.code == 1
    assert "确认移动 1 个文件" in res.err        # 问句写在 stderr 上
    assert "[取消]" in res.err
    assert src.exists()


def test_move_yes_moves_files_and_saves_record(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])
    target = work_dir / "dst"
    ops = work_dir / "ops.json"

    res = run_cli(["move", str(results), "-t", str(target), "-y", "--ops", str(ops),
                   *config_args(work_dir)])

    assert res.code == 0
    moved = target / "ABP-123" / "ABP-123.mp4"      # 默认按番号建子文件夹
    assert moved.exists() and not src.exists()
    assert "移动完成：成功 1，跳过 0，失败 0" in res.out
    records = json.loads(ops.read_text(encoding="utf-8"))
    assert [r["target_filename"] for r in records] == ["ABP-123.mp4"]


def test_move_folder_flag_puts_files_flat(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])
    target = work_dir / "dst"

    res = run_cli(["move", str(results), "-t", str(target), "-y", "--no-folder",
                   *config_args(work_dir)])

    assert res.code == 0
    assert (target / "ABP-123.mp4").exists()


def test_move_video_naming_code_renames(work_dir):
    src = make_file(work_dir / "src" / "乱七八糟的名字.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])
    target = work_dir / "dst"

    res = run_cli(["move", str(results), "-t", str(target), "-y",
                   "--video-naming", "code", *config_args(work_dir)])

    assert res.code == 0
    assert (target / "ABP-123" / "ABP-123.mp4").exists()


def test_move_only_filters_and_is_case_insensitive(work_dir):
    first = make_file(work_dir / "src" / "ABP-123.mp4")
    second = make_file(work_dir / "src" / "SSIS-001.mkv")
    results = write_results(
        work_dir / "results.json", [entry(first, "ABP-123"), entry(second, "SSIS-001")])
    target = work_dir / "dst"

    res = run_cli(["move", str(results), "-t", str(target), "-y",
                   "--only", "abp-123", *config_args(work_dir)])

    assert res.code == 0
    assert (target / "ABP-123").exists()
    assert not (target / "SSIS-001").exists()
    assert second.exists()
    assert "只处理指定的番号：ABP-123" in res.out


def test_move_only_without_match_exits_2(work_dir):
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])

    res = run_cli(["move", str(results), "-t", str(work_dir / "dst"), "-y",
                   "--only", "NOPE-999", *config_args(work_dir)])

    assert res.code == 2
    assert "一个都没匹配到" in res.err
    assert src.exists()


def test_move_unrecognized_results_are_left_alone(work_dir):
    src = make_file(work_dir / "src" / "random.mkv")
    results = write_results(work_dir / "results.json", [entry(src, "random.mkv", "original")])
    target = work_dir / "dst"

    res = run_cli(["move", str(results), "-t", str(target), "--dry-run",
                   *config_args(work_dir)])

    assert res.code == 0
    assert "1 条保持原样，不参与移动" in res.out
    assert "没有需要处理的文件" in res.err
    assert src.exists()


@pytest.mark.parametrize(
    ("handling", "expected"),
    [("rename", "ABP-123_01.mp4"), ("overwrite", "ABP-123.mp4"), ("skip", None)],
)
def test_move_conflict_handling(work_dir, handling, expected):
    src = make_file(work_dir / "src" / "ABP-123.mp4", "新的")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])
    target = work_dir / "dst"
    existing = make_file(target / "ABP-123" / "ABP-123.mp4", "旧的")

    res = run_cli(["move", str(results), "-t", str(target), "-y",
                   "--on-conflict", handling, *config_args(work_dir)])

    assert res.code == 0
    if expected is None:
        assert existing.read_text(encoding="utf-8") == "旧的"
        assert src.exists()                        # 跳过的文件留在原地
        assert "跳过 1" in res.out
    else:
        assert (target / "ABP-123" / expected).read_text(encoding="utf-8") == "新的"


def test_move_warns_when_two_files_share_a_target(work_dir):
    """批内重名只在"覆盖"时才是真冲突：默认的「重命名」会把第二个改成 _01。"""
    first = make_file(work_dir / "a" / "ABP-123.mp4")
    second = make_file(work_dir / "b" / "ABP-123.mp4")
    results = write_results(
        work_dir / "results.json", [entry(first, "ABP-123"), entry(second, "ABP-123")])
    target = str(work_dir / "dst")

    renamed = run_cli(["move", str(results), "-t", target, "--dry-run",
                       *config_args(work_dir)])
    assert "目标路径冲突" not in renamed.err
    assert "重命名后移动  1" in renamed.out

    overwritten = run_cli(["move", str(results), "-t", target, "--dry-run",
                           "--on-conflict", "overwrite", *config_args(work_dir)])
    assert "目标路径冲突" in overwritten.err                 # stderr 上始终看得见
    assert "会写到同样的目标路径" in overwritten.out          # 详情在预览里
    assert "无法找回" in overwritten.out
    assert "--on-conflict rename" in overwritten.out


def test_move_saves_partial_record_when_interrupted(work_dir, monkeypatch):
    """Ctrl+C 之后已经移动的文件必须有记录，否则没有挽回手段。"""
    first = make_file(work_dir / "src" / "ABP-123.mp4")
    second = make_file(work_dir / "src" / "SSIS-001.mkv")
    results = write_results(
        work_dir / "results.json", [entry(first, "ABP-123"), entry(second, "SSIS-001")])
    ops = work_dir / "ops.json"

    def fake_execute(self, planned, progress=None, on_moved=None):
        for p in planned:
            on_moved(MoveOperation(
                original_path=str(p.src), moved_to=str(p.dst),
                original_filename=p.src.name, target_filename=p.dst.name,
                extracted_code=p.extracted_code))
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.FileProcessor, "execute", fake_execute)

    res = run_cli(["move", str(results), "-t", str(work_dir / "dst"), "-y",
                   "--ops", str(ops), *config_args(work_dir)])

    assert res.code == 1
    assert "[中断]" in res.err
    assert "移动记录已保存" in res.err
    assert len(json.loads(ops.read_text(encoding="utf-8"))) == 2


def test_move_rejects_json_that_is_not_a_list(work_dir):
    results = work_dir / "results.json"
    results.write_text('{"不是": "数组"}', encoding="utf-8")

    res = run_cli(["move", str(results), "-t", str(work_dir / "dst"), "-y",
                   *config_args(work_dir)])

    assert res.code == 1
    assert "不是数组" in res.err


def test_move_uses_target_from_recent_history(work_dir):
    """窗口里选过一次目标目录，命令行不该再要求 -t。"""
    src = make_file(work_dir / "src" / "ABP-123.mp4")
    results = write_results(work_dir / "results.json", [entry(src, "ABP-123")])
    target = work_dir / "dst"
    write_config(work_dir, {"processor": {"recent_target_dirs": [str(target)]}})

    res = run_cli(["move", str(results), "-y", *config_args(work_dir)])

    assert res.code == 0
    assert (target / "ABP-123" / "ABP-123.mp4").exists()


# ---------------------------------------------------------------------------
# 撤回
# ---------------------------------------------------------------------------
def make_moved_pair(work_dir):
    """造一份"已经移动过"的现场：文件在目标位置 + 一份记录文件。"""
    original = work_dir / "src" / "ABP-123.mp4"
    moved = work_dir / "dst" / "ABP-123" / "ABP-123.mp4"
    make_file(moved)
    ops_file = work_dir / "ops.json"
    FileProcessor.save_operations([
        MoveOperation(
            original_path=str(original), moved_to=str(moved),
            original_filename="ABP-123.mp4", target_filename="ABP-123.mp4",
            extracted_code="ABP-123"),
    ], ops_file)
    return original, moved, ops_file


def test_undo_restores_files_and_removes_record(work_dir):
    original, moved, ops_file = make_moved_pair(work_dir)

    res = run_cli(["undo", "--ops", str(ops_file), "-y", *config_args(work_dir)])

    assert res.code == 0
    assert original.exists() and not moved.exists()
    assert "撤回完成：成功 1，失败 0" in res.out
    assert "记录文件已删除" in res.out
    assert not ops_file.exists()


def test_undo_missing_record_exits_1(work_dir):
    res = run_cli(["undo", "--ops", str(work_dir / "nope.json"), *config_args(work_dir)])

    assert res.code == 1
    assert "找不到移动记录" in res.err


def test_undo_dry_run_keeps_everything(work_dir):
    original, moved, ops_file = make_moved_pair(work_dir)

    res = run_cli(["undo", "--ops", str(ops_file), "--dry-run", *config_args(work_dir)])

    assert res.code == 0
    assert "将把下列文件搬回原位置" in res.out
    assert "预览结束" in res.out
    assert moved.exists() and not original.exists()
    assert ops_file.exists()


def test_undo_refuses_without_yes_in_non_interactive(work_dir):
    _original, moved, ops_file = make_moved_pair(work_dir)

    res = run_cli(["undo", "--ops", str(ops_file), *config_args(work_dir)])

    assert res.code == 2
    assert "必须加 --yes" in res.err
    assert moved.exists()


def test_undo_keeps_record_when_original_path_is_occupied(work_dir):
    original, moved, ops_file = make_moved_pair(work_dir)
    make_file(original, "别人又放回来的")

    res = run_cli(["undo", "--ops", str(ops_file), "-y", *config_args(work_dir)])

    assert res.code == 1
    assert "原路径已有同名文件" in res.err
    assert "撤回完成：成功 0，失败 1" in res.out
    assert "记录文件已保留" in res.out
    assert ops_file.exists()
    assert original.read_text(encoding="utf-8") == "别人又放回来的"   # 没被覆盖


def test_undo_can_run_twice_after_partial_run(work_dir):
    """撤回是幂等的：已经搬回去的算完成，记录才能收尾删掉。"""
    original, _moved, ops_file = make_moved_pair(work_dir)

    first = run_cli(["undo", "--ops", str(ops_file), "-y", *config_args(work_dir)])
    assert first.code == 0 and not ops_file.exists()

    # 模拟"上次撤回只完成了一部分"：文件已经回到原位，记录还在
    FileProcessor.save_operations([
        MoveOperation(
            original_path=str(original),
            moved_to=str(work_dir / "dst" / "ABP-123" / "ABP-123.mp4"),
            original_filename="ABP-123.mp4", target_filename="ABP-123.mp4",
            extracted_code="ABP-123"),
    ], ops_file)

    second = run_cli(["undo", "--ops", str(ops_file), "-y", *config_args(work_dir)])

    assert second.code == 0
    assert "撤回完成：成功 1，失败 0" in second.out
    assert "已经撤回过的" in second.out
    assert not ops_file.exists()


# ---------------------------------------------------------------------------
# 配置（只读）
# ---------------------------------------------------------------------------
def test_config_path_does_not_create_the_file(work_dir):
    missing = work_dir / "nope.json"

    res = run_cli(["config", "--path", "-c", str(missing)])

    assert res.code == 0
    assert res.out.strip() == str(missing)
    assert not missing.exists()


def test_config_list_shows_labels_and_sections(work_dir):
    write_config(work_dir, {
        "scraper": {"output_filename": "my.json"},
        "processor": {"existing_file_handling": "跳过"},
    })

    res = run_cli(["config", *config_args(work_dir)])

    assert res.code == 0
    for expected in ("[扫描]", "[移动]", "[通用]", "配置文件", "输出文件名"):
        assert expected in res.out
    assert "my.json" in res.out
    assert "跳过" in res.out
    assert "known_alpha_prefixes" not in res.out       # 隐藏字段不列
    assert "共 " in res.out and "项）" in res.out       # 几百个前缀要截断


def test_config_json_output_is_parseable(work_dir):
    res = run_cli(["config", "--json", *config_args(work_dir)])

    assert res.code == 0
    payload = json.loads(res.out)
    assert payload["scraper"]["output_filename"] == "scraper_results.json"
    assert "processor" in payload


# ---------------------------------------------------------------------------
# 输出编码：Windows 中文控制台是 cp936
# ---------------------------------------------------------------------------
def test_every_cli_output_survives_a_gbk_console(work_dir):
    """所有输出都必须能用 GBK 编码，否则 zh-CN 控制台上会直接崩。"""
    src = work_dir / "src"
    make_file(src / "ABP-123.mp4")
    make_file(src / "另一个名字.mkv")
    first = make_file(work_dir / "a" / "ABP-123.mp4")
    second = make_file(work_dir / "b" / "ABP-123.mp4")
    results = write_results(
        work_dir / "results.json", [entry(first, "ABP-123"), entry(second, "ABP-123")])
    _original, _moved, ops_file = make_moved_pair(work_dir)

    runs = [
        run_cli(["scan", str(src), "--no-reports", "-v", *config_args(work_dir)]),
        run_cli(["move", str(results), "-t", str(work_dir / "dst"), "--dry-run",
                 *config_args(work_dir)]),
        run_cli(["undo", "--ops", str(ops_file), "--dry-run", *config_args(work_dir)]),
        run_cli(["config", *config_args(work_dir)]),
    ]

    for res in runs:
        for text in (res.out, res.err, res.data):
            text.encode("gbk")          # 编不出来就会在这里炸


def test_soften_stream_replaces_unencodable_characters():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk")

    with pytest.raises(UnicodeEncodeError):
        stream.write("✓")               # 未处理时就是这个下场

    cli._soften_stream(stream)
    stream.write("✓")
    stream.flush()

    assert b"?" in raw.getvalue()


def test_display_width_aligns_chinese_labels():
    assert display_width("输出目录") == 8
    assert display_width("abc") == 3
    assert pad_display("输出目录", 12) == "输出目录    "
    assert pad_display("abc", 3) == "abc"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def test_no_subcommand_prints_help(work_dir):
    res = run_cli([])

    assert res.code == 0
    assert "usage:" in res.out
    for sub in ("scan", "move", "undo", "config"):
        assert sub in res.out


def test_version_flag():
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "0.4.5" in out.getvalue()


def test_unknown_subcommand_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense"])

    assert excinfo.value.code == 2


def test_missing_positional_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        main(["scan"])

    assert excinfo.value.code == 2


def test_default_prog_ignores_python_c(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-c"])
    assert cli.default_prog() == "javscraper"

    monkeypatch.setattr(sys, "argv", [str(Path("av_scraper") / "__main__.py")])
    assert cli.default_prog() == "python -m av_scraper"


def test_no_args_still_launches_the_gui(monkeypatch):
    """双击 exe / python run.py 这条老路径不能被命令行改动影响（也不该真开窗口）。"""
    from av_scraper import __main__ as entry

    calls: list[str] = []
    monkeypatch.setattr(entry, "_enable_dpi_awareness", lambda: calls.append("dpi"))
    monkeypatch.setattr(entry, "launch_gui", lambda: calls.append("gui"))

    assert entry.main([]) == 0
    assert calls == ["dpi", "gui"]          # 先声明 DPI，再建窗口


def test_args_are_dispatched_to_the_cli(monkeypatch):
    from av_scraper import __main__ as entry

    monkeypatch.setattr(
        entry, "launch_gui", lambda: pytest.fail("带参数时不应该启动图形界面"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as excinfo:
        entry.main(["--version"])

    assert excinfo.value.code == 0
    assert "0.4.5" in out.getvalue()

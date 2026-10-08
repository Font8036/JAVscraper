"""命令行模式：扫描 / 移动 / 撤回。

设计原则：

- 只依赖核心（``scraper`` / ``processor`` / ``reports`` / ``config``），
  **完全不 import tkinter**，所以能在没有图形界面的地方跑（服务器、计划任务、SSH）。
- 与图形界面共用同一份 ``config.json`` 和同一份移动记录
  （``log/move_operations.json``）：命令行里移的文件，可以回窗口里点「撤回移动」。
- 会动文件的动作默认先预览、再确认；非交互环境（管道 / 计划任务）必须显式 ``--yes``。
- 输出只用 GBK 能编码的字符。Windows 中文控制台是 cp936，``✓`` ``✗`` ``↳`` ``⚠``
  这类符号编不出来会直接抛 UnicodeEncodeError（本项目实测），所以一律用文字表达；
  另外给 stdout/stderr 加 ``errors="replace"`` 兜底，防止文件名里的生僻字把整条命令搞崩。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from .config import AppConfig, ProcessorConfig, ScraperConfig
from .paths import config_path, log_dir
from .processor import (
    FileProcessor,
    MoveOperation,
    PlannedOperation,
    find_target_collisions,
)
from .reports import make_run_directory, save_csv, save_json, save_text_report, to_dict
from .scraper import CodeExtractor, ScrapeResult
from .texts import (
    COLLISION_TAIL_CLI,
    build_collision_message,
    move_status_text,
    undo_status_text,
)

# 移动记录的文件名，与 gui/move_tab.py 保持一致（两边可以互相撤回）
OPS_FILENAME = "move_operations.json"

EXIT_OK = 0          # 成功（含"没有需要处理的文件"）
EXIT_FAILED = 1      # 有文件没处理成功、写盘失败、被中断、用户取消
EXIT_USAGE = 2       # 参数用法问题（argparse 也用这个）

# 摘要里默认最多列几条明细；--limit 0 表示不限
DEFAULT_LIMIT = 20

# 边移动边落盘记录的间隔（中途 Ctrl+C 也不至于丢掉已移动文件的记录）
FLUSH_EVERY = 200

_VIDEO_NAMING = {"keep": "保留原文件名", "code": "按提取码命名"}
_ATTACHMENT_NAMING = {
    "keep": "保留原文件名",
    "code": "按提取码命名",
    "follow": "跟随视频命名",
}
_CONFLICT_HANDLING = {"skip": "跳过", "overwrite": "覆盖", "rename": "重命名"}

_PLAN_STATUS_LABEL = {
    "move": "移动",
    "rename": "重命名后移动",
    "overwrite": "覆盖（目标已存在，会被替换）",
    "skip": "跳过（目标已存在）",
}


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def display_width(text: str) -> int:
    """按终端里的显示宽度算长度：中文/全角算 2，其余算 1（对齐要用）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad_display(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def align_rows(rows: Sequence[tuple[str, str]], indent: str = "") -> list[str]:
    """把「标签: 值」对齐成两列。"""
    if not rows:
        return []
    width = max(display_width(label) for label, _ in rows)
    return [f"{indent}{pad_display(label, width)}  {value}" for label, value in rows]


def _is_interactive() -> bool:
    """能不能问用户？只看 stdin——它是 input() 真正读的那个流。

    （stdout 被重定向到文件时照样可以问，提示语写在 stderr 上就不会混进文件。）
    """
    stream = sys.stdin
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError):
        return False


def _soften_stream(stream: TextIO) -> None:
    """把编不出来的字符换成 ``?``，而不是让整条命令崩在最后一行。"""
    try:
        stream.reconfigure(errors="replace")     # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass


class Console:
    """命令行的输入输出出口。

    测试里注入 ``StringIO`` 和一个假的 ``ask`` 就能脱离终端跑，
    ``interactive`` 也可以直接指定，不用依赖真实的 isatty()。
    """

    def __init__(
        self,
        *,
        out: TextIO | None = None,
        err: TextIO | None = None,
        data_out: TextIO | None = None,
        ask: Callable[[str], str] | None = None,
        interactive: bool | None = None,
        verbose: bool = False,
        quiet: bool = False,
    ):
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        # 数据出口（例如 --json - 的结果）：永远不被 -q 抑制，也不与摘要混在一起
        self.data_out = data_out if data_out is not None else sys.stdout
        self.ask = ask if ask is not None else input
        self.interactive = _is_interactive() if interactive is None else interactive
        self.verbose = verbose
        self.quiet = quiet

    def out_line(self, text: str = "") -> None:
        """给人看的输出；-q 时闭嘴。"""
        if not self.quiet:
            print(text, file=self.out)

    def out_lines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.out_line(line)

    def data_line(self, text: str) -> None:
        """给机器读的输出（--json -）：不受 -q 影响。"""
        print(text, file=self.data_out)

    def err_line(self, text: str = "") -> None:
        """错误 / 提醒：永远打印，且走 stderr，不会污染管道里的数据。"""
        print(text, file=self.err)

    def confirm(self, prompt: str) -> bool:
        """问一个 y/N 问题。非交互环境一律当作"否"。"""
        if not self.interactive:
            return False
        print(prompt, end=" ", file=self.err, flush=True)
        try:
            answer = self.ask("")
        except (EOFError, KeyboardInterrupt):
            self.err_line("")
            return False
        return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def resolve_config_path(raw: str | None) -> Path:
    return Path(raw).expanduser() if raw else config_path()


def load_app_config(path: Path) -> tuple[AppConfig, list[str]]:
    """读配置。和图形界面一样：文件不存在就按默认值新建一份。"""
    return AppConfig.load_with_notices(path)


def show_notices(ui: Console, notices: Sequence[str]) -> None:
    for notice in notices:
        ui.err_line(f"[提醒] {notice}")


def load_config_or_fail(ui: Console, args: argparse.Namespace) -> AppConfig | None:
    """读配置并把"程序替你改过的地方"告诉用户；读不出来就返回 None。"""
    try:
        cfg, notices = load_app_config(resolve_config_path(args.config))
    except OSError as e:
        ui.err_line(f"[错误] 配置文件读不出来：{e}")
        return None
    show_notices(ui, notices)
    return cfg


def default_target_directory(pcfg: ProcessorConfig) -> str:
    """目标目录的默认值：配置里的 target_directory，否则用历史里最近用过的一个。

    图形界面的移动页就是这么预填的（见 widgets/history_path.py），命令行跟着来，
    免得同一台机器上窗口里能跑、命令行却报"没指定目标目录"。
    """
    if pcfg.target_directory:
        return pcfg.target_directory
    return pcfg.recent_target_dirs[0] if pcfg.recent_target_dirs else ""


def apply_overrides(pcfg: ProcessorConfig, args: argparse.Namespace) -> ProcessorConfig:
    """把命令行参数覆盖到配置上（没给的就用配置里的）。"""
    overrides: dict[str, Any] = {}
    if getattr(args, "target", None):
        overrides["target_directory"] = args.target
    if getattr(args, "folder", None) is not None:
        overrides["move_to_extracted_folder"] = args.folder
    if getattr(args, "video_naming", None):
        overrides["video_naming"] = _VIDEO_NAMING[args.video_naming]
    if getattr(args, "attachment_naming", None):
        overrides["attachment_naming"] = _ATTACHMENT_NAMING[args.attachment_naming]
    if getattr(args, "on_conflict", None):
        overrides["existing_file_handling"] = _CONFLICT_HANDLING[args.on_conflict]
    return replace(pcfg, **overrides) if overrides else pcfg


def split_only(values: Sequence[str] | None) -> set[str]:
    """--only 可以给多次，也可以一次给逗号分隔的多个番号。"""
    codes: set[str] = set()
    for raw in values or ():
        for part in str(raw).split(","):
            part = part.strip()
            if part:
                codes.add(part.upper())
    return codes


def filter_planned(
    planned: Sequence[PlannedOperation], only: set[str],
) -> list[PlannedOperation]:
    if not only:
        return list(planned)
    return [p for p in planned if p.extracted_code.upper() in only]


# ---------------------------------------------------------------------------
# 报告路径
# ---------------------------------------------------------------------------
def resolve_report_paths(args: argparse.Namespace, scfg: ScraperConfig) -> dict[str, Path]:
    """要写哪几份报告。``--no-reports`` 与 ``--json -`` 都返回空字典（不落盘）。

    文件名与图形界面一致：JSON 用配置里的「输出文件名」，
    另外两份固定叫 extraction_report.txt / extraction_table.csv。
    """
    if args.no_reports or args.json == "-":
        return {}
    if args.json:
        base = Path(args.json).expanduser().parent
        json_path = Path(args.json).expanduser()
    else:
        if args.output_dir:
            base = Path(args.output_dir).expanduser()
        elif scfg.output_directory:
            base = Path(scfg.output_directory).expanduser()
        else:
            base = log_dir()
        if not args.flat:
            base = make_run_directory(base)
        json_path = base / scfg.output_filename
    return {
        "json": json_path,
        "txt": base / "extraction_report.txt",
        "csv": base / "extraction_table.csv",
    }


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------
def _take(items: Sequence[Any], limit: int) -> list[Any]:
    """limit <= 0 表示不限。"""
    return list(items) if limit <= 0 else list(items[:limit])


def format_scan_summary(
    results: Sequence[ScrapeResult],
    *,
    directory: Path,
    limit: int = DEFAULT_LIMIT,
    verbose: bool = False,
) -> list[str]:
    total = len(results)
    direct = sum(1 for r in results if r.is_extracted and not r.inherited)
    inherited = sum(1 for r in results if r.inherited)
    original = total - direct - inherited
    ratio = (direct + inherited) / total * 100 if total else 0.0

    lines = align_rows([
        ("扫描目录", str(directory)),
        ("文件总数", str(total)),
        ("直接提取", str(direct)),
        ("父目录继承", str(inherited)),
        ("保持原样", str(original)),
        ("提取率", f"{ratio:.1f}%"),
    ])

    unrecognized = [r for r in results if not r.is_extracted]
    if unrecognized:
        shown = _take(unrecognized, limit)
        lines += ["", f"未识别（{len(unrecognized)}）："]
        lines += [f"  - {r.filename}" for r in shown]
        if len(shown) < len(unrecognized):
            lines.append(f"  ……另有 {len(unrecognized) - len(shown)} 个未列出"
                         f"（--limit 0 可以全部列出）")

    if verbose:
        lines += ["", "明细："]
        for r in results:
            mark = "继承" if r.inherited else ("提取" if r.is_extracted else "原样")
            lines.append(f"  [{mark}] {r.extracted_code}  <-  {r.filename}")
    return lines


def format_plan_preview(
    planned: Sequence[PlannedOperation],
    *,
    json_path: Path,
    target: str,
    conflict: str,
    recognized: int,
    total: int,
    only: set[str],
    limit: int = DEFAULT_LIMIT,
    verbose: bool = False,
) -> list[str]:
    lines = align_rows([
        ("输入 JSON", str(json_path)),
        ("目标目录", target or "(未设置)"),
        ("文件冲突处理", conflict),
    ])
    lines += [
        "",
        f"扫描结果 {total} 条，其中 {recognized} 条识别到番号"
        f"（{total - recognized} 条保持原样，不参与移动）",
    ]
    if only:
        lines.append(f"只处理指定的番号：{'、'.join(sorted(only))}")

    counts = Counter(p.status for p in planned)
    lines.append(f"本次将处理 {len(planned)} 个文件：")
    labels = [s for s in ("move", "rename", "overwrite", "skip") if counts.get(s)]
    if labels:
        width = max(display_width(_PLAN_STATUS_LABEL[s]) for s in labels)
        lines += [
            f"  {pad_display(_PLAN_STATUS_LABEL[s], width)}  {counts[s]}" for s in labels
        ]

    overwrites = [p for p in planned if p.status == "overwrite"]
    if overwrites:
        shown = _take(overwrites, limit)
        lines += ["", f"会被替换掉的目标（{len(overwrites)}）："]
        lines += [f"  {p.dst}" for p in shown]
        if len(shown) < len(overwrites):
            lines.append(f"  ……另有 {len(overwrites) - len(shown)} 处未列出")

    skips = [p for p in planned if p.status == "skip"]
    if skips:
        shown = _take(skips, limit)
        lines += ["", f"会跳过（目标已存在）（{len(skips)}）："]
        lines += [f"  {p.src.name} → {p.dst}" for p in shown]
        if len(shown) < len(skips):
            lines.append(f"  ……另有 {len(skips) - len(shown)} 个未列出")

    if verbose:
        lines += ["", "明细："]
        lines += [
            f"  [{_PLAN_STATUS_LABEL.get(p.status, p.status)}] {p.src} → {p.dst}"
            for p in planned
        ]
    return lines


def format_move_result(
    success: int,
    skipped: int,
    failed: int,
    *,
    ops_file: Path | None,
    verbose_notes: Sequence[str] = (),
) -> list[str]:
    lines = [f"移动完成：成功 {success}，跳过 {skipped}，失败 {failed}"]
    if ops_file is not None:
        lines.append(f"移动记录已保存：{ops_file}（可以用 undo 撤回）")
    if verbose_notes:
        lines += ["", *verbose_notes]
    return lines


def format_undo_preview(
    ops: Sequence[MoveOperation],
    *,
    ops_file: Path,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    lines = align_rows([("记录文件", str(ops_file)), ("记录条数", str(len(ops)))])
    shown = _take(ops, limit)
    lines += ["", "将把下列文件搬回原位置："]
    lines += [f"  {o.moved_to} → {o.original_path}" for o in shown]
    if len(shown) < len(ops):
        lines.append(f"  ……另有 {len(ops) - len(shown)} 条未列出（--limit 0 可以全部列出）")
    return lines


def format_undo_result(
    success: int,
    failed: int,
    *,
    ops_file: Path,
    removed: bool,
    already: int = 0,
) -> list[str]:
    lines = [f"撤回完成：成功 {success}，失败 {failed}"]
    if already:
        lines.append(f"（其中 {already} 个是之前已经撤回过的）")
    if removed:
        lines.append(f"记录文件已删除：{ops_file}")
    else:
        lines.append(f"记录文件已保留：{ops_file}"
                     f"（处理掉失败原因后可以再执行一次 undo）")
    return lines


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        if not value:
            return "(空)"
        text = "、".join(str(v) for v in value)
        if display_width(text) > 60:
            return "、".join(str(v) for v in value[:8]) + f" ……（共 {len(value)} 项）"
        return text
    if value == "":
        return "(空)"
    return str(value)


def format_config(cfg: AppConfig) -> list[str]:
    """把配置按 GUI 里的分组列出来（用的是同一份 label 元数据）。"""
    lines: list[str] = []
    for title, obj in (("扫描", cfg.scraper), ("移动", cfg.processor), ("通用", cfg)):
        rows: list[tuple[str, str]] = []
        for f in dataclasses.fields(obj):
            if f.metadata.get("hidden"):
                continue
            rows.append((f.metadata.get("label", f.name),
                         _format_value(getattr(obj, f.name))))
        if not rows:
            continue
        lines.append(f"[{title}]")
        lines += align_rows(rows, indent="  ")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_scan(args: argparse.Namespace, ui: Console) -> int:
    directory = Path(args.directory).expanduser()
    if not directory.exists():
        ui.err_line(f"[错误] 目录不存在：{directory}")
        return EXIT_FAILED
    if not directory.is_dir():
        ui.err_line(f"[错误] 不是目录：{directory}")
        return EXIT_FAILED

    cfg = load_config_or_fail(ui, args)
    if cfg is None:
        return EXIT_FAILED
    scfg = cfg.scraper

    recursive = scfg.recursive_processing if args.recursive is None else args.recursive
    extractor = CodeExtractor(scfg)
    if ui.verbose:
        ui.out_line(f"递归处理子目录：{'是' if recursive else '否'}")
    try:
        results = extractor.scan_directory(directory, recursive=recursive)
    except OSError as e:
        ui.err_line(f"[错误] 扫描失败：{e}")
        return EXIT_FAILED

    exit_code = EXIT_OK
    written: list[Path] = []
    # 只算一次：算里面会按时间戳建运行目录，多次调用会得到不同的目录
    report_paths = resolve_report_paths(args, scfg)
    for kind, writer in (("json", save_json), ("txt", save_text_report), ("csv", save_csv)):
        path = report_paths.get(kind)
        if path is None:
            continue
        try:
            writer(results, path)
        except OSError as e:
            ui.err_line(f"[错误] 写入失败：{path}（{e}）")
            exit_code = EXIT_FAILED
        else:
            written.append(path)

    if args.json == "-":
        ui.data_line(json.dumps(
            [to_dict(r) for r in results], ensure_ascii=False, indent=2))

    ui.out_lines(format_scan_summary(
        results, directory=directory, limit=args.limit, verbose=args.verbose))
    if written:
        ui.out_line("")
        ui.out_lines([f"已保存  {p}" for p in written])
    return exit_code


def cmd_move(args: argparse.Namespace, ui: Console) -> int:
    json_path = Path(args.json).expanduser()
    if not json_path.exists():
        ui.err_line(f"[错误] 找不到扫描结果文件：{json_path}")
        return EXIT_FAILED

    cfg = load_config_or_fail(ui, args)
    if cfg is None:
        return EXIT_FAILED

    pcfg = apply_overrides(cfg.processor, args)
    target = default_target_directory(pcfg)
    if not target:
        ui.err_line("[错误] 没有目标目录。请用 -t 指定，"
                    "或者先在图形界面的移动页里选一次目标目录。")
        return EXIT_FAILED
    pcfg = replace(pcfg, target_directory=target, input_json=str(json_path))

    try:
        results = FileProcessor.load_results(json_path)
    except (OSError, ValueError) as e:
        ui.err_line(f"[错误] 扫描结果读不出来：{e}")
        return EXIT_FAILED
    if not isinstance(results, list):
        ui.err_line("[错误] 扫描结果 JSON 的内容不是数组，可能不是本工具生成的文件")
        return EXIT_FAILED
    recognized = sum(
        1 for r in results if isinstance(r, dict) and r.get("status") == "extracted")

    proc = FileProcessor(
        pcfg,
        video_exts=cfg.scraper.video_extensions,
        attachment_exts=cfg.scraper.attachment_extensions,
    )
    only = split_only(args.only)
    try:
        planned = filter_planned(proc.plan(results), only)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        ui.err_line(f"[错误] 扫描结果的内容不对，没法规划移动：{e}")
        return EXIT_FAILED
    if only and not planned:
        ui.err_line(f"[错误] --only 指定的番号一个都没匹配到：{'、'.join(sorted(only))}")
        return EXIT_USAGE

    ui.out_lines(format_plan_preview(
        planned,
        json_path=json_path,
        target=target,
        conflict=pcfg.existing_file_handling,
        recognized=recognized,
        total=len(results),
        only=only,
        limit=args.limit,
        verbose=args.verbose,
    ))

    collisions = find_target_collisions(planned)
    if collisions:
        ui.err_line(f"[提醒] 本批有 {len(collisions)} 处目标路径冲突"
                    f"（同一个目标会被多个文件写入）")
        ui.out_line("")
        ui.out_lines(build_collision_message(collisions, tail=COLLISION_TAIL_CLI).splitlines())

    if not planned:
        ui.err_line("[提示] 没有需要处理的文件，没有做任何改动")
        return EXIT_OK

    if args.dry_run:
        ui.out_line("")
        ui.out_line("[预览结束] 没有改动任何文件（去掉 --dry-run 才会真的移动）")
        return EXIT_OK

    if not args.yes:
        if not ui.interactive:
            ui.err_line("[错误] 当前不是交互式终端，执行前必须加 --yes 明确确认")
            return EXIT_USAGE
        if not ui.confirm(f"确认移动 {len(planned)} 个文件？[y/N]"):
            ui.err_line("[取消] 没有做任何改动")
            return EXIT_FAILED

    ops_file = Path(args.ops).expanduser() if args.ops else log_dir() / OPS_FILENAME
    record: list[MoveOperation] = []
    failures: list[str] = []
    skips: list[str] = []
    save_warned = False

    def save_record() -> bool:
        nonlocal save_warned
        try:
            proc.save_operations(record, ops_file)
        except OSError as e:
            if not save_warned:
                save_warned = True
                ui.err_line(f"[错误] 移动记录写入失败：{ops_file}（{e}）")
            return False
        save_warned = False
        return True

    def on_moved(op: MoveOperation) -> None:
        record.append(op)
        if len(record) % FLUSH_EVERY == 0:
            save_record()

    def on_progress(idx: int, total: int, op: PlannedOperation, status: str) -> None:
        text = move_status_text(op, status)
        if not text:
            if ui.verbose:
                ui.out_line(f"  [{idx}/{total}] {op.src.name}")
            return
        if status == "skip":
            skips.append(text)
            if ui.verbose:
                ui.out_line(f"  {text}")
        else:
            failures.append(text)
            ui.err_line(text)

    ui.out_line(f"开始移动 {len(planned)} 个文件……")
    try:
        _ops, success, skipped, failed = proc.execute(
            planned, progress=on_progress, on_moved=on_moved)
    except KeyboardInterrupt:
        ui.err_line("")
        ui.err_line(f"[中断] 已停止，本次共移动了 {len(record)} 个文件")
        if record and save_record():
            ui.err_line(f"[中断] 移动记录已保存：{ops_file}，可以用 undo 撤回")
        return EXIT_FAILED

    saved = bool(record) and save_record()
    ui.out_lines(format_move_result(
        success, skipped, failed,
        ops_file=ops_file if saved else None,
        verbose_notes=[f"  {t}" for t in skips] if ui.verbose else (),
    ))
    if failures:
        ui.err_line(f"[错误] 有 {failed} 个文件没有处理成功，明细见上方")
    return EXIT_OK if failed == 0 else EXIT_FAILED


def cmd_undo(args: argparse.Namespace, ui: Console) -> int:
    ops_file = Path(args.ops).expanduser() if args.ops else log_dir() / OPS_FILENAME
    if not ops_file.exists():
        ui.err_line(f"[错误] 找不到移动记录：{ops_file}")
        return EXIT_FAILED

    # 撤回只认记录文件，不读配置
    proc = FileProcessor(ProcessorConfig())
    try:
        ops = proc.load_operations(ops_file)
    except (OSError, ValueError, TypeError) as e:
        ui.err_line(f"[错误] 记录文件读不出来：{e}")
        return EXIT_FAILED
    if not ops:
        ui.err_line(f"[提示] 记录文件里没有内容：{ops_file}")
        return EXIT_OK

    ui.out_lines(format_undo_preview(ops, ops_file=ops_file, limit=args.limit))

    if args.dry_run:
        ui.out_line("")
        ui.out_line("[预览结束] 没有改动任何文件")
        return EXIT_OK

    if not args.yes:
        if not ui.interactive:
            ui.err_line("[错误] 当前不是交互式终端，执行前必须加 --yes 明确确认")
            return EXIT_USAGE
        if not ui.confirm(f"确认把这 {len(ops)} 个文件搬回原位置？[y/N]"):
            ui.err_line("[取消] 没有做任何改动")
            return EXIT_FAILED

    already: list[str] = []

    def on_progress(op: MoveOperation, status: str) -> None:
        text = undo_status_text(op, status)
        if not text:
            return
        if status == "already":
            already.append(text)
            if ui.verbose:
                ui.out_line(f"  {text}")
        else:
            ui.err_line(text)

    try:
        success, failed = proc.undo(ops_file, progress=on_progress)
    except KeyboardInterrupt:
        ui.err_line("")
        ui.err_line("[中断] 已停止撤回")
        ui.err_line(f"[中断] 记录文件仍保留：{ops_file}，可以再执行一次 undo 接着撤")
        return EXIT_FAILED

    ui.out_lines(format_undo_result(
        success, failed,
        ops_file=ops_file,
        removed=not ops_file.exists(),
        already=len(already),
    ))
    return EXIT_OK if failed == 0 else EXIT_FAILED


def cmd_config(args: argparse.Namespace, ui: Console) -> int:
    path = resolve_config_path(args.config)
    if args.path:
        # 只看路径：连配置都不读，免得顺手把它创建出来
        ui.out_line(str(path))
        return EXIT_OK

    cfg = load_config_or_fail(ui, args)
    if cfg is None:
        return EXIT_FAILED
    if args.json:
        ui.out_line(json.dumps(dataclasses.asdict(cfg), ensure_ascii=False, indent=2))
    else:
        ui.out_line(f"配置文件  {path}")
        ui.out_line("")
        ui.out_lines(format_config(cfg))
    return EXIT_OK


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def default_prog() -> str:
    """用法信息里显示的程序名。"""
    argv0 = sys.argv[0] if sys.argv else ""
    stem = Path(argv0).stem
    if not stem or stem == "__main__":
        return "python -m av_scraper"
    if stem.startswith("-"):        # python -c "..."
        return "javscraper"
    return stem


def build_parser(prog: str = "javscraper") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="文件名刮削与文件整理工具 —— 命令行模式（不需要图形界面）。",
        epilog="退出码：0 成功；1 有文件没处理成功 / 被中断 / 用户取消；"
               "2 参数用法问题（含非交互环境少了 --yes）。",
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"{prog} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<子命令>")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", metavar="PATH",
                        help="配置文件（默认：程序目录下的 config.json）")
    verbosity = common.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true",
                           help="列出每个文件的明细")
    verbosity.add_argument("-q", "--quiet", action="store_true",
                           help="不输出摘要，只输出错误")

    scan = sub.add_parser(
        "scan", parents=[common], help="扫描目录、提取番号、导出结果",
        description="扫描目录，从文件名里提取番号，并导出 JSON / TXT / CSV。")
    scan.add_argument("directory", metavar="目录", help="要扫描的目录")
    scan.add_argument("-r", "--recursive", action=argparse.BooleanOptionalAction,
                      default=None, help="递归处理子目录（默认取配置）")
    scan.add_argument("-o", "--output-dir", metavar="DIR",
                      help="报告输出目录（默认取配置里的「输出目录」，为空则用 log/）")
    scan.add_argument("--flat", action="store_true",
                      help="不建时间戳子目录，直接写进输出目录")
    scan.add_argument("--json", metavar="PATH",
                      help="结果 JSON 的路径（- 表示写到标准输出）")
    scan.add_argument("--no-reports", action="store_true",
                      help="不写任何报告文件，只看结果")
    scan.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N",
                      help="摘要里最多列几条明细，0 表示不限（默认 %(default)s）")
    scan.set_defaults(func=cmd_scan)

    move = sub.add_parser(
        "move", parents=[common], help="按扫描结果移动 / 重命名文件",
        description="按扫描结果的番号把文件移动 / 重命名到目标目录。"
                    "默认先预览再问一次，确认后才动文件。")
    move.add_argument("json", metavar="结果.json", help="scan 生成的扫描结果 JSON")
    move.add_argument("-t", "--target", metavar="DIR",
                      help="目标目录（默认取配置，其次取历史里最近用过的）")
    move.add_argument("--folder", action=argparse.BooleanOptionalAction, default=None,
                      help="按番号建子文件夹（默认取配置）")
    move.add_argument("--video-naming", choices=sorted(_VIDEO_NAMING),
                      metavar="{keep,code}", help="视频命名方式（keep 保留原名，code 按番号）")
    move.add_argument("--attachment-naming", choices=sorted(_ATTACHMENT_NAMING),
                      metavar="{keep,code,follow}",
                      help="附件命名方式（follow 跟随同目录视频）")
    move.add_argument("--on-conflict", choices=sorted(_CONFLICT_HANDLING),
                      metavar="{skip,overwrite,rename}",
                      help="目标已存在同名文件时怎么办")
    move.add_argument("--only", action="append", metavar="番号",
                      help="只处理这些番号，可以给多次或用逗号分隔")
    move.add_argument("--dry-run", action="store_true", help="只预览，不动任何文件")
    move.add_argument("-y", "--yes", action="store_true",
                      help="不再确认，直接执行（非交互环境必须加）")
    move.add_argument("--ops", metavar="PATH",
                      help=f"移动记录文件（默认 log/{OPS_FILENAME}）")
    move.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N",
                      help="预览里最多列几条明细，0 表示不限（默认 %(default)s）")
    move.set_defaults(func=cmd_move)

    undo = sub.add_parser(
        "undo", parents=[common], help="撤回上一次移动",
        description="按移动记录把文件搬回原位置。可以重复执行："
                    "已经搬回去的会被跳过，没搬完的接着搬。")
    undo.add_argument("--ops", metavar="PATH",
                      help=f"移动记录文件（默认 log/{OPS_FILENAME}）")
    undo.add_argument("--dry-run", action="store_true", help="只列出会撤回什么")
    undo.add_argument("-y", "--yes", action="store_true",
                      help="不再确认，直接执行（非交互环境必须加）")
    undo.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N",
                      help="预览里最多列几条，0 表示不限（默认 %(default)s）")
    undo.set_defaults(func=cmd_undo)

    config = sub.add_parser(
        "config", parents=[common], help="查看当前配置（只读）",
        description="显示当前生效的配置，以及配置文件的路径。改配置请用图形界面。")
    config.add_argument("--json", action="store_true", help="用 JSON 输出")
    config.add_argument("--path", action="store_true", help="只输出配置文件路径")
    config.set_defaults(func=cmd_config)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    prog: str | None = None,
    console: Console | None = None,
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(prog or default_prog())
    args = parser.parse_args(argv)

    if console is None:
        # --json - ：摘要走 stderr，标准输出只留 JSON，管道里拿到的就是可以
        # 直接喂给 jq 之类工具的纯数据
        console = Console(out=sys.stderr if getattr(args, "json", None) == "-" else None)
    console.verbose = bool(getattr(args, "verbose", False))
    console.quiet = bool(getattr(args, "quiet", False))

    if getattr(args, "func", None) is None:
        # 帮助也走 console.out：测试里注入的流才能收到它
        parser.print_help(file=console.out)
        return EXIT_OK

    try:
        return int(args.func(args, console))
    except KeyboardInterrupt:
        console.err_line("")
        console.err_line("[中断] 已退出")
        return EXIT_FAILED


def run(argv: Sequence[str] | None = None, *, prog: str | None = None) -> int:
    """命令行入口：先给输出流加编码兜底，再交给 main()。"""
    _soften_stream(sys.stdout)
    _soften_stream(sys.stderr)
    return main(argv, prog=prog)

"""扫描结果导出：JSON / 文本 / CSV。"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path

from .fileio import write_via_temp
from .scraper import ScrapeResult


def make_run_directory(base: str | Path) -> Path:
    """在 base 下按时间戳建立一次运行的输出目录。"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def to_dict(r: ScrapeResult) -> dict:
    """扫描结果的字典形式。命令行 ``scan --json -`` 也用这个，保证两边字段一致。"""
    return {
        "file_path": r.file_path,
        "filename": r.filename,
        "extracted_code": r.extracted_code,
        "status": r.status,
        "file_size": r.file_size,
        "inherited": r.inherited,
        "manually_edited": r.manually_edited,   # ← 新增
    }


def save_json(results: list[ScrapeResult], path: Path) -> None:
    # 先写临时文件再替换：写到一半崩了也不会留下半截 JSON
    data = json.dumps([to_dict(r) for r in results], ensure_ascii=False, indent=2)
    write_via_temp(path, lambda tmp: tmp.write_text(data, encoding="utf-8"))


def save_text_report(results: list[ScrapeResult], path: Path) -> None:
    total = len(results)
    direct = sum(1 for r in results if r.is_extracted and not r.inherited)
    inherited = sum(1 for r in results if r.inherited)
    original = total - direct - inherited
    ratio = (direct + inherited) / total * 100 if total else 0

    lines = [
        "文件名刮削器处理报告",
        "=" * 50,
        f"处理时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"总文件数: {total}",
        f"直接提取: {direct}",
        f"父目录继承: {inherited}",
        f"保持原样: {original}",
        f"总成功率: {ratio:.1f}%",
        "",
        "=" * 50,
        "文件处理详情:",
        "-" * 50,
    ]
    for r in results:
        if r.manually_edited:
            icon = "✎"
        elif r.inherited:
            icon = "↳"
        elif r.is_extracted:
            icon = "✓"
        else:
            icon = "✗"
        lines.append(f"{icon} {r.filename} -> {r.extracted_code}")

    text = "\n".join(lines)
    write_via_temp(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))


def save_csv(results: list[ScrapeResult], path: Path) -> None:
    extracted = sorted(
        (r for r in results if r.is_extracted),
        key=lambda r: r.extracted_code,
    )
    direct = sum(1 for r in results if r.is_extracted and not r.inherited)
    inherited = sum(1 for r in results if r.inherited)
    total = len(results)
    ratio = f"{(direct + inherited) / total * 100:.1f}%" if total else "0%"

    def _write(tmp: Path) -> None:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["原始文件名", "提取出的信息", "来源"])
            for r in extracted:
                source = "父目录继承" if r.inherited else "文件名"
                writer.writerow([r.filename, r.extracted_code, source])
            writer.writerow([])
            writer.writerow(["统计信息"])
            writer.writerow(["总文件数", total])
            writer.writerow(["直接提取", direct])
            writer.writerow(["父目录继承", inherited])
            writer.writerow(["总成功率", ratio])
            writer.writerow(["生成时间",
                             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])

    write_via_temp(path, _write)

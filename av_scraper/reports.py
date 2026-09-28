"""扫描结果导出：JSON / 文本 / CSV。"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path

from .scraper import ScrapeResult


def make_run_directory(base: str | Path) -> Path:
    """在 base 下按时间戳建立一次运行的输出目录。"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base) / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _to_dict(r: ScrapeResult) -> dict:
    return {
        "file_path": r.file_path,
        "filename": r.filename,
        "extracted_code": r.extracted_code,
        "status": r.status,
        "file_size": r.file_size,
        "inherited": r.inherited,
    }


def save_json(results: list[ScrapeResult], path: Path) -> None:
    path.write_text(
        json.dumps([_to_dict(r) for r in results],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
        if r.inherited:
            icon = "↳"
        elif r.is_extracted:
            icon = "✓"
        else:
            icon = "✗"
        lines.append(f"{icon} {r.filename} -> {r.extracted_code}")

    path.write_text("\n".join(lines), encoding="utf-8")


def save_csv(results: list[ScrapeResult], path: Path) -> None:
    extracted = sorted(
        (r for r in results if r.is_extracted),
        key=lambda r: r.extracted_code,
    )
    direct = sum(1 for r in results if r.is_extracted and not r.inherited)
    inherited = sum(1 for r in results if r.inherited)
    total = len(results)
    ratio = f"{(direct + inherited) / total * 100:.1f}%" if total else "0%"

    with open(path, "w", newline="", encoding="utf-8") as f:
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
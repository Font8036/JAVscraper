"""生成字幕猫爬取结果的 Excel 报告。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import xlsxwriter

FAILED_BG = "#FFC7CE"       # 浅红：失败
MISMATCH_BG = "#FFF2CC"     # 浅黄：番号不一致


def build_excel(
    records: list[dict],
    output_excel: str | Path,
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    log = on_log or (lambda _s: None)

    output_excel = Path(output_excel)
    output_excel.parent.mkdir(parents=True, exist_ok=True)

    workbook = xlsxwriter.Workbook(str(output_excel))
    try:
        worksheet = workbook.add_worksheet("Sheet1")

        header_fmt = workbook.add_format({
            "bold": True, "font_size": 11,
            "align": "center", "valign": "vcenter",
            "bg_color": "#D9D9D9",
        })
        normal_fmt = workbook.add_format({"valign": "vcenter"})
        hyperlink_fmt = workbook.add_format({
            "valign": "vcenter",
            "font_color": "blue",
            "underline": 1,
        })

        failed_cell_fmt = workbook.add_format({
            "valign": "vcenter", "bg_color": FAILED_BG,
        })
        failed_link_fmt = workbook.add_format({
            "valign": "vcenter", "bg_color": FAILED_BG,
            "font_color": "blue", "underline": 1,
        })
        mismatch_cell_fmt = workbook.add_format({
            "valign": "vcenter", "bg_color": MISMATCH_BG,
        })
        mismatch_link_fmt = workbook.add_format({
            "valign": "vcenter", "bg_color": MISMATCH_BG,
            "font_color": "blue", "underline": 1,
        })

        # 列宽
        column_widths = {
            0: 14,   # 目标番号
            1: 14,   # 搜索结果数量
            2: 14,   # 实际番号
            3: 60,   # 名称
            4: 12,   # 源语言
            5: 10,   # 下载量
            6: 22,   # 状态
        }
        for c, w in column_widths.items():
            worksheet.set_column(c, c, w)

        headers = ["目标番号", "搜索结果数量", "实际番号",
                   "名称", "源语言", "下载量", "状态"]
        for c, h in enumerate(headers):
            worksheet.write(0, c, h, header_fmt)

        for i, rec in enumerate(records):
            row_idx = i + 1
            status = str(rec.get("status", ""))

            is_failed = (
                "下载失败" in status
                or status == "未搜到"
                or status == "已停止"
            )
            is_mismatch = "番号不一致" in status

            if is_failed:
                cell_fmt = failed_cell_fmt
                link_fmt = failed_link_fmt
            elif is_mismatch:
                cell_fmt = mismatch_cell_fmt
                link_fmt = mismatch_link_fmt
            else:
                cell_fmt = normal_fmt
                link_fmt = hyperlink_fmt

            worksheet.write(row_idx, 0, rec.get("target", ""), cell_fmt)

            # 搜索结果数量：超链接文本
            count = rec.get("search_count", 0)
            url = rec.get("_search_url", "")
            if url:
                worksheet.write_url(
                    row_idx, 1, url, link_fmt, str(count))
            else:
                worksheet.write(row_idx, 1, count, cell_fmt)

            worksheet.write(row_idx, 2, rec.get("actual_code", ""), cell_fmt)
            worksheet.write(row_idx, 3, rec.get("title", ""), cell_fmt)
            worksheet.write(row_idx, 4, rec.get("source_lang", ""), cell_fmt)
            worksheet.write(
                row_idx, 5, rec.get("downloads", 0), cell_fmt)
            worksheet.write(row_idx, 6, status, cell_fmt)

        worksheet.freeze_panes(1, 0)
    finally:
        workbook.close()

    # 路径由调用方在"发布"成功后写日志 —— 这里拿到的可能只是临时文件路径
    log("数据处理完成")
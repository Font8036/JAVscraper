"""把爬取结果 CSV 生成带封面嵌入的 Excel 报告。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import xlsxwriter

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from .parse import parse_row

MAX_ROW_HEIGHT_PT = 409.5
B_COL_WIDTH_CHARS = 22


def build_excel(
    input_csv: str | Path,
    output_excel: str | Path,
    cover_dir: str | Path,
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    log = on_log or (lambda s: None)

    df = pd.read_csv(input_csv, encoding="utf-8-sig", dtype=str)
    log(f"成功读取数据，共 {len(df)} 条记录")
    df = df.reset_index(drop=True)

    required = ["链接", "番号", "名称", "信息", "评论"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"输入文件缺少必要的列：{col}")

    records = [parse_row(row.to_dict()) for _, row in df.iterrows()]

    cover_dir = Path(cover_dir)
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

        column_widths = {
            0: 12.32, 1: B_COL_WIDTH_CHARS, 2: 35, 3: 20,
            4: 3, 5: 12, 6: 25, 7: 3, 8: 50,
        }
        for c, w in column_widths.items():
            worksheet.set_column(c, c, w)

        col_width_px = B_COL_WIDTH_CHARS * 7 + 5

        headers = ["番号", "封面", "名称", "演员", "", "评分", "类别", "", "评论"]
        for c, h in enumerate(headers):
            worksheet.write(0, c, h, header_fmt)

        for i, rec in enumerate(records):
            row_idx = i + 1
            worksheet.write(row_idx, 0, rec["番号"] or "", normal_fmt)
            worksheet.write(row_idx, 2, rec["名称"] or "", normal_fmt)
            worksheet.write(row_idx, 3, rec["演员"] or "", normal_fmt)
            worksheet.write(row_idx, 4, "", normal_fmt)

            if rec["评分"] and rec["链接"]:
                worksheet.write_url(
                    row_idx, 5, rec["链接"], hyperlink_fmt, rec["评分"])
            else:
                worksheet.write(row_idx, 5, rec["评分"] or "", normal_fmt)

            worksheet.write(row_idx, 6, rec["类别"] or "", normal_fmt)
            worksheet.write(row_idx, 7, "", normal_fmt)
            worksheet.write(row_idx, 8, rec["评论"] or "", normal_fmt)

            fanhao = rec["番号"]
            if not fanhao:
                continue

            img_path = _find_cover(cover_dir, fanhao)
            if img_path is None:
                continue

            row_height_pt = _estimate_row_height(img_path, col_width_px, log)
            row_height_pt = max(40.0, min(row_height_pt, MAX_ROW_HEIGHT_PT))
            worksheet.set_row(row_idx, row_height_pt)

            try:
                worksheet.embed_image(row_idx, 1, str(img_path))
            except Exception as e:
                log(f"嵌入图片 {fanhao} 失败: {e}")

        worksheet.freeze_panes(1, 0)
    finally:
        workbook.close()

    log(f"数据处理完成，已保存到 {output_excel}")


def _find_cover(cover_dir: Path, fanhao: str) -> Optional[Path]:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = cover_dir / f"{fanhao}{ext}"
        if candidate.exists():
            return candidate
    return None


def _estimate_row_height(
    img_path: Path,
    col_width_px: float,
    log: Callable[[str], None],
) -> float:
    if not HAS_PIL:
        return 200.0
    try:
        with PILImage.open(img_path) as im:
            w, h = im.size
        if w <= 0:
            return 200.0
        # Excel 行高单位是 pt，像素→pt 的近似换算系数 0.75
        return col_width_px * h / w * 0.75
    except Exception as e:
        log(f"读取图片尺寸失败 {img_path}: {e}")
        return 200.0
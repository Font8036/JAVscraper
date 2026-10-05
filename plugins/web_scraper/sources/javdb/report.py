"""把爬取结果 CSV 生成带封面嵌入的 Excel 报告。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import csv
import xlsxwriter

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from .parse import parse_row
from .parse import is_matched

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

    input_path = Path(input_csv)
    if not input_path.exists():
        raise FileNotFoundError(f"CSV 不存在：{input_path}")

    with open(input_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    log(f"成功读取数据，共 {len(rows)} 条记录")

    required = ["链接", "番号", "名称", "信息", "评论"]
    for col in required:
        if col not in fieldnames:
            raise ValueError(f"输入文件缺少必要的列：{col}")

    # 空值统一成空字符串（DictReader 给的是 None 或 ""）
    def _clean_row(r: dict) -> dict:
        return {k: ("" if v is None else v) for k, v in r.items()}

    records = [parse_row(_clean_row(r)) for r in rows]

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
        # ---- 整行填充色：目标番号与刮到的番号不一致时使用 ----
        MISMATCH_BG = "#FFFFBE"    # 浅黄
        mismatch_cell_fmt = workbook.add_format({
            "valign": "vcenter",
            "bg_color": MISMATCH_BG,
        })
        mismatch_link_fmt = workbook.add_format({
            "valign": "vcenter",
            "bg_color": MISMATCH_BG,
            "font_color": "blue",
            "underline": 1,
        })
        # ---- 整行填充色：刮削失败的行 ----
        FAILED_BG = "#FFC7CE"     # 浅红
        failed_cell_fmt = workbook.add_format({
            "valign": "vcenter",
            "bg_color": FAILED_BG,
        })

        column_widths = {
            0: 12.32,   # 目标番号
            1: 12.32,   # 刮到的番号
            2: B_COL_WIDTH_CHARS,   # 封面
            3: 35,   # 名称
            4: 20,   # 演员
            5: 3,    # 空
            6: 12,   # 评分
            7: 25,   # 类别
            8: 3,    # 空
            9: 50,   # 评论
        }
        for c, w in column_widths.items():
            worksheet.set_column(c, c, w)

        col_width_px = B_COL_WIDTH_CHARS * 7 + 5

        headers = ["目标番号", "实际番号", "封面", "名称", "演员", "", "评分", "类别", "", "评论"]
        for c, h in enumerate(headers):
            worksheet.write(0, c, h, header_fmt)

        mismatch_fmt = workbook.add_format({
            "valign": "vcenter", "font_color": "red", "bold": True,
        })

        for i, rec in enumerate(records):
            row_idx = i + 1

            target = rec.get("目标番号", "") or ""
            scraped = rec.get("番号", "") or ""
            failed = bool(target and not scraped)
            mismatched = bool(target and scraped and not is_matched(target, scraped))

            if failed:
                cell_fmt = failed_cell_fmt
                link_fmt = failed_cell_fmt
            elif mismatched:
                cell_fmt = mismatch_cell_fmt
                link_fmt = mismatch_link_fmt
            else:
                cell_fmt = normal_fmt
                link_fmt = hyperlink_fmt

            # 0 目标番号 —— 失败行只写这一列
            worksheet.write(row_idx, 0, target, cell_fmt)
            # 1 刮到的番号
            worksheet.write(row_idx, 1, scraped, cell_fmt)
            # 2 封面：先写空值（让填充色生效），稍后再 embed_image
            worksheet.write(row_idx, 2, "", cell_fmt)
            # 3 名称
            worksheet.write(row_idx, 3, rec["名称"] or "", cell_fmt)
            # 4 演员
            worksheet.write(row_idx, 4, rec["演员"] or "", cell_fmt)
            # 5 空列
            worksheet.write(row_idx, 5, "", cell_fmt)
            # 6 评分（超链接）
            if rec["链接"]:
                # 有链接就一定写成超链接，评分缺失时显示"无评分"
                display = rec["评分"] or "无评分"
                worksheet.write_url(
                    row_idx, 6, rec["链接"], link_fmt, display)
            else:
                # 无链接（通常是爬取失败的行），只显示评分
                worksheet.write(row_idx, 6, rec["评分"] or "", cell_fmt)
            # 7 类别
            worksheet.write(row_idx, 7, rec["类别"] or "", cell_fmt)
            # 8 空列
            worksheet.write(row_idx, 8, "", cell_fmt)
            # 9 评论
            worksheet.write(row_idx, 9, rec["评论"] or "", cell_fmt)

            # ---- 封面嵌入 ----
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
                worksheet.embed_image(row_idx, 2, str(img_path))   # ← 列号从 1 改成 2
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
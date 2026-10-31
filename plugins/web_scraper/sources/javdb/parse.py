"""字段提取：从 javdb 详情页文本里解析演员、评分、类别、评论。

纯函数模块，无网络依赖，可以独立单测。
"""

from __future__ import annotations

import math
import re
from typing import Optional
from pathlib import Path

def _is_blank(value) -> bool:
    if value is None:
        return True
    try:
        if math.isnan(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def clean_text(text) -> str:
    """清理文本，保留换行，压缩多余空格与空行。"""
    if _is_blank(text):
        return ""
    text = str(text).replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def extract_actors(info_text: str) -> str:
    """提取演员，去掉 No.xx / JavDB 排名杂质。兼容 演員/演员。"""
    if not info_text:
        return ""
    m = re.search(r"演[員员][:：]\s*(.*?)(?=\s*想看)", info_text, re.DOTALL)
    if not m:
        return ""
    actors = m.group(1)
    actors = re.split(r"\n\s*(?:No\.\d+|JavDB)", actors, maxsplit=1)[0]
    actors = re.sub(r"\s+", " ", actors).strip()
    actors = re.sub(r"\s*,\s*", ", ", actors)
    return actors


def extract_rating(info_text: str) -> str:
    """提取评分，返回 分数/人数。兼容 評分/评分、人評價/人评价。"""
    if not info_text:
        return ""
    pattern = r"[評评]分[:：]\s*(\d+\.?\d*)\s*分\s*,\s*由\s*(\d+)\s*人[評评][價价]"
    match = re.search(pattern, info_text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return ""


def extract_category(info_text: str) -> str:
    """提取类别。兼容 類別/类别、演員/演员。"""
    if not info_text:
        return ""
    pattern = r"[類类][別别][:：]\s*(.*?)(?=\s*演[員员][:：])"
    match = re.search(pattern, info_text, re.DOTALL)
    if not match:
        return ""
    category = match.group(1).strip()
    category = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s,，··]", "", category)
    category = re.sub(r"\s+", " ", category).strip()
    category = re.sub(r"\s*,\s*", ", ", category)
    return category


def extract_comments(comment_text: str) -> str:
    """提取最多 3 条评论，用中文句号连接。兼容简体与繁体。"""
    if not comment_text:
        return ""
    if "暫無內容" in comment_text or "暂无内容" in comment_text:
        return "暂无评论"

    date_pattern = r"\d{4}-\d{1,2}-\d{1,2}"
    date_matches = list(re.finditer(date_pattern, comment_text))
    if not date_matches:
        return "暂无评论"

    comments: list[str] = []
    for i, m in enumerate(date_matches[:3]):
        start = m.end()
        end = (
            date_matches[i + 1].start()
            if i + 1 < len(date_matches)
            else len(comment_text)
        )
        content = comment_text[start:end]
        content = re.split(r"[檢检][舉举]|更多短[評评]", content, maxsplit=1)[0]
        content = re.sub(r"\s+", " ", content).strip()
        if content:
            comments.append(content)

    return "。".join(comments) if comments else "暂无评论"


def parse_row(row: dict) -> dict:
    """把爬取到的一行（含"信息"、"评论"）转成结构化字段。"""
    info_clean = clean_text(row.get("信息", ""))
    comment_clean = clean_text(row.get("评论", ""))
    return {
        "目标番号": row.get("目标番号", ""),      # ← 新增
        "链接": row.get("链接", ""),
        "番号": row.get("番号", ""),
        "名称": row.get("名称", ""),
        "演员": extract_actors(info_clean),
        "评分": extract_rating(info_clean),
        "类别": extract_category(info_clean),
        "评论": extract_comments(comment_clean),
    }

def normalize_code(code: str) -> str:
    """把番号归一化后比较：去掉分隔符、大写、去掉 FC2 的 PPV。"""
    if not code:
        return ""
    s = str(code).upper()
    s = s.replace("FC2PPV", "FC2").replace("FC2-PPV", "FC2")
    # 去掉 - 和 _
    s = s.replace("-", "").replace("_", "")
    return s


def is_matched(target: str, scraped: str) -> bool:
    """判断刮到的番号是否与目标番号一致。"""
    return bool(target) and normalize_code(target) == normalize_code(scraped)

def _check_contains(target: str, scraped: str) -> bool:
    """包含与边界校验（当提取器失效时的兜底方案）"""
    t = target.upper().replace("_", "-").replace(" ", "-")
    s = scraped.upper().replace("_", "-").replace(" ", "-")
    
    idx = 0
    while True:
        idx = s.find(t, idx)
        if idx == -1:
            return False
            
        # 左边界校验：如果 target 以字母开头，前一个字符不能是字母
        left_ok = True
        if t[0].isalpha() and idx > 0:
            if s[idx - 1].isalpha():
                left_ok = False
                
        # 右边界校验：如果 target 以数字结尾，后一个字符不能是数字
        right_ok = True
        if t[-1].isdigit() and idx + len(t) < len(s):
            if s[idx + len(t)].isdigit():
                right_ok = False
                
        if left_ok and right_ok:
            return True
        idx += 1

def is_consistent(target: str, scraped: str, extractor) -> bool:
    """综合判断目标番号和网页刮到的番号是否一致。"""
    if not target or not scraped:
        return False
        
    # 1. 先尝试提取器（这会解决 ABC-123-C 与 ABC-123 一致的问题）
    t_code = extractor.extract(target)
    s_code = extractor.extract(scraped)
    if t_code and s_code:
        return normalize_code(t_code) == normalize_code(s_code)

    # 2. 提取器失效，直接去符号比对
    if normalize_code(target) == normalize_code(scraped):
        return True
        
    # 3. 包含与边界校验（解决 heydouga-4030-2143 与 4030-2143）
    return _check_contains(target, scraped)

def get_cover_stem(target: str, scraped: str, extractor) -> str:
    """决定封面图片的文件名主干（不含扩展名）。"""
    if is_consistent(target, scraped, extractor):
        return target  # 一致，直接用标准番号

    # 不一致，尝试提取网页番号的标准形式
    s_code = extractor.extract(scraped)
    if s_code:
        return s_code

    # 提取器也认不出，用清理后的网页原始番号
    return re.sub(r'[\\/*?:"<>|]', "", scraped).strip()

def find_existing_cover(cover_dir: Path, target: str, scraped: str, extractor) -> Optional[Path]:
    """按优先级查找已有封面图片。"""
    if not cover_dir.exists():
        return None
        
    exts = [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]
    
    # 候选文件名集合（按优先级）
    candidates = [target]
    
    s_code = extractor.extract(scraped)
    if s_code and s_code != target:
        candidates.append(s_code)
        
    clean_scraped = re.sub(r'[\\/*?:"<>|]', "", scraped).strip()
    if clean_scraped and clean_scraped not in candidates:
        candidates.append(clean_scraped)
        
    for name in candidates:
        for ext in exts:
            p = cover_dir / f"{name}{ext}"
            if p.exists():
                return p
    return None

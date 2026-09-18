"""字段提取：从 javdb 详情页文本里解析演员、评分、类别、评论。

纯函数模块，无网络依赖，可以独立单测。
"""

from __future__ import annotations

import math
import re


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
        "链接": row.get("链接", ""),
        "番号": row.get("番号", ""),
        "名称": row.get("名称", ""),
        "演员": extract_actors(info_clean),
        "评分": extract_rating(info_clean),
        "类别": extract_category(info_clean),
        "评论": extract_comments(comment_clean),
    }
"""解析搜索结果、语言映射、优先级排序。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ---- 语言映射 ----
_LANG_MAP = {
    "chinese": "中文",
    "chinese (simplified)": "简体中文",
    "chinese (traditional)": "繁体中文",
    "english": "英语",
    "japanese": "日语",
    "korean": "韩语",
    "spanish": "西班牙语",
    "french": "法语",
    "german": "德语",
    "russian": "俄语",
    "thai": "泰语",
    "vietnamese": "越南语",
    "italian": "意大利语",
    "portuguese": "葡萄牙语",
    "arabic": "阿拉伯语",
    "dutch": "荷兰语",
    "polish": "波兰语",
    "turkish": "土耳其语",
    "indonesian": "印尼语",
    "malay": "马来语",
}


def translate_lang(raw: str) -> str:
    if not raw:
        return ""
    key = raw.strip().lower()
    return _LANG_MAP.get(key, raw.strip())


# ---- 正则 ----
_TRANSLATED_RE = re.compile(
    r"\(\s*translated\s+from\s+([^)]+?)\s*\)",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(
    r"(\d+)\s+subtitles?\s+found",
    re.IGNORECASE,
)


# ---- 数据类 ----
@dataclass
class SearchResult:
    title: str                    # 完整文本
    title_clean: str              # 去掉 (translated from Xxx) 后缀
    source_lang_raw: str          # 原始语言名，如 "Chinese"
    source_lang: str              # 映射后中文，如 "中文"
    translated_from_chinese: bool # 是否 translated from Chinese
    has_thumbs_up: bool
    downloads: int
    languages: int
    href: str


# ---- 工具函数 ----
def extract_result_count(text: str) -> int:
    if not text:
        return 0
    m = _COUNT_RE.search(text)
    return int(m.group(1)) if m else 0


def parse_title(raw: str) -> tuple[str, str, str, bool]:
    """返回 (去掉后缀的标题, 源语言原文, 源语言中文, 是否 from Chinese)。"""
    if not raw:
        return "", "", "", False
    m = _TRANSLATED_RE.search(raw)
    if not m:
        return raw.strip(), "", "", False

    lang_raw = m.group(1).strip()
    lang_cn = translate_lang(lang_raw)
    is_chinese = bool(re.search(r"\bChinese\b", lang_raw, re.IGNORECASE))

    clean = _TRANSLATED_RE.sub("", raw).strip()
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean, lang_raw, lang_cn, is_chinese


def normalize_code(code: str) -> str:
    """番号归一化，用于比较。"""
    s = (code or "").upper()
    s = s.replace("FC2PPV", "FC2").replace("FC2-PPV", "FC2")
    return s.replace("-", "").replace("_", "")


def is_matched(target: str, scraped: str) -> bool:
    return bool(target) and normalize_code(target) == normalize_code(scraped)


# ---- 选择最佳结果 ----
def pick_best(
    results: list[SearchResult],
    target_code: str,
    extractor,  # CodeExtractor
) -> Optional[SearchResult]:
    """四级优先级：番号一致 > 👍 > translated from Chinese > 下载量。"""
    if not results:
        return None

    target_norm = normalize_code(target_code)

    def get_code(r: SearchResult) -> str:
        code = extractor.extract(r.title_clean)
        return code or ""

    def sort_key(r: SearchResult):
        code = get_code(r)
        level1 = 1 if code and normalize_code(code) == target_norm else 0
        level2 = 1 if r.has_thumbs_up else 0
        level3 = 1 if r.translated_from_chinese else 0
        level4 = r.downloads
        return (level1, level2, level3, level4)

    return max(results, key=sort_key)
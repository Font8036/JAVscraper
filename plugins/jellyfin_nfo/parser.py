"""字段解析：演员 / 评分 / 类别 / 列名拆分。"""

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


def split_columns(s: str) -> list[str]:
    """把 "演员,演员2，演员3;演员4" 拆成 ["演员", "演员2", "演员3", "演员4"]。"""
    if not s:
        return []
    return [c.strip() for c in re.split(r"[,，;；]+", s) if c.strip()]


def parse_actors(actor_str) -> list[dict]:
    """解析演员字符串，返回 [{'name', 'aka', 'gender'}, ...]。

    输入示例： "张三（张三丰）♂ 李四,王五（王麻子）♀"
    """
    if _is_blank(actor_str):
        return []
    s = str(actor_str)
    temp = s.replace("，", ",").replace("、", ",")
    parts = re.split(r"[,，、\s]+", temp)

    result: list[dict] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        gender = ""
        if "♂" in part:
            gender = "Male"
            part = part.replace("♂", "")
        elif "♀" in part:
            gender = "Female"
            part = part.replace("♀", "")

        aliases = re.findall(r"（[^）]*）", part)
        main_name = re.sub(r"（[^）]*）", "", part).strip()
        aliases_clean = [a.strip("（）") for a in aliases]

        if not main_name and aliases_clean:
            main_name = aliases_clean[0]
            aliases_clean = aliases_clean[1:] if len(aliases_clean) > 1 else []

        aka = aliases_clean[0] if aliases_clean else ""
        result.append({"name": main_name, "aka": aka, "gender": gender})

    return result


def parse_genres(genre_str) -> list[str]:
    """解析类别字符串。"""
    if _is_blank(genre_str):
        return []
    s = str(genre_str)
    temp = s.replace("，", ",").replace("、", ",")
    return [g.strip() for g in re.split(r"[,，、\s]+", temp) if g.strip()]


def parse_rating(rating_str) -> tuple:
    """解析 "4.0/18" 或 "4.0"。返回 (分数, 人数)。"""
    if _is_blank(rating_str):
        return None, None
    s = str(rating_str).strip()

    score = None
    count = None

    if "/" in s:
        parts = s.split("/")
        try:
            score = float(parts[0].strip())
        except (ValueError, TypeError):
            score = None
        if len(parts) >= 2:
            try:
                count = int(parts[1].strip())
            except (ValueError, TypeError):
                count = None
    else:
        try:
            score = float(s)
        except (ValueError, TypeError):
            score = None

    return score, count
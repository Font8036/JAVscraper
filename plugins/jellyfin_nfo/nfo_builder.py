"""生成 Jellyfin 兼容的 NFO XML。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom


def build_nfo(movie_data: dict, poster_filename) -> str:
    root = ET.Element("movie")

    code = movie_data.get("code", "")
    title = movie_data.get("title") or code

    if title:
        full_title = f"{code} {title}" if code and title != code else title
        ET.SubElement(root, "title").text = full_title

    if code:
        ET.SubElement(root, "id").text = code
        uid = ET.SubElement(root, "uniqueid")
        uid.text = code
        uid.set("type", "custom")

    # ---- 评分：每个 rating 列一个 <rating> ----
    ratings = movie_data.get("ratings", [])
    for i, (score, count) in enumerate(ratings):
        r = ET.SubElement(root, "rating")
        r.text = str(score)
        r.set("name", "database" if i == 0 else f"database{i+1}")
        if count is not None:
            r.set("votes", str(count))

    # criticrating 取第一个评分的副本，兼容旧播放器
    if ratings:
        ET.SubElement(root, "criticrating").text = str(ratings[0][0])

    # ---- 类别：并集 ----
    for g in movie_data.get("genres", []):
        if g:
            ET.SubElement(root, "genre").text = g

    # ---- 演员：并集 ----
    for actor in movie_data.get("actors", []):
        if not actor.get("name"):
            continue
        a = ET.SubElement(root, "actor")
        ET.SubElement(a, "name").text = actor["name"]
        if actor.get("aka"):
            ET.SubElement(a, "role").text = actor["aka"]
        if actor.get("gender"):
            ET.SubElement(a, "type").text = actor["gender"]

    # ---- plot / outline ----
    personal = movie_data.get("personal_comment", "")
    user = movie_data.get("user_comment", "")

    plot_parts = []
    if personal:
        plot_parts.append(f"【个人评价】\n{personal}")
    if user:
        plot_parts.append(f"【网友评论】\n{user}")
    if plot_parts:
        ET.SubElement(root, "plot").text = "\n\n".join(plot_parts)

    if personal:
        ET.SubElement(root, "outline").text = personal

    # ---- 封面（只写文件名，Jellyfin 会从视频同级目录查找） ----
    if poster_filename:
        ET.SubElement(root, "thumb").text = poster_filename

    rough = ET.tostring(root, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
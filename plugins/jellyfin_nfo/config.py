"""Jellyfin NFO 插件自己的配置。"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from av_scraper.fileio import write_via_temp


_NFO_MODE_CHOICES = [
    "每个视频生成同名 NFO",
    "只生成 movie.nfo",
    "两种都生成",
]
_COVER_NAMING_CHOICES = [
    "两种都复制",
    "与视频同名",
    "用番号命名",
]


@dataclass
class JellyfinNfoConfig:
    # ---- 路径 ----
    input_excel: str = field(
        default="", metadata={
            "label": "输入 Excel", "kind": "file",
            "ext": ".xlsx",                   # ← 新增
            "tooltip": "刮削结果的 Excel（通常是 JavDB 插件生成的）。",
        }
    )
    movie_root: str = field(
        default="", metadata={
            "label": "电影文件目录", "kind": "dir",
            "tooltip": "待处理的电影文件夹根目录，每个子文件夹名应为番号。",
        }
    )
    cover_source_dir: str = field(
        default="", metadata={
            "label": "封面源目录", "kind": "dir",
            "tooltip": "从 Excel 提取的封面保存到哪里；生成 NFO 时从这里复制封面。",
        }
    )

    # ---- Excel 解析 ----
    sheet_index: int = field(
        default=1, metadata={
            "label": "工作表序号", "kind": "int",
            "row_group": "excel_pos",
            "tooltip": "第几个工作表（从 1 开始）。",
        }
    )
    header_row: int = field(
        default=1, metadata={
            "label": "列名所在行", "kind": "int",
            "row_group": "excel_pos",
            "tooltip": "列名在第几行。\n下方输入框中即为生成.nfo时，相应信息的列名。\n其中番号，封面，标题只允许输入一个列名；演员，评分，类别，个人评论，网友评论可以输入多个列名，用逗号分隔。",
        }
    )

    code_column: str = field(
        default="番号", metadata={
            "label": "番号", "kind": "str", "width": 8,
            "row_group": "excel_cols",
        }
    )
    cover_column: str = field(
        default="封面", metadata={
            "label": "封面", "kind": "str", "width": 8,
            "row_group": "excel_cols",
        }
    )
    title_column: str = field(
        default="标题", metadata={
            "label": "标题", "kind": "str", "width": 8,
            "row_group": "excel_cols",
        }
    )
    actor_columns: str = field(
        default="演员", metadata={
            "label": "演员", "kind": "str", "width": 8,
            "row_group": "excel_cols",
            "tooltip": "支持多列，用 , 分隔。",
        }
    )
    rating_columns: str = field(
        default="评分", metadata={
            "label": "评分", "kind": "str", "width": 8,
            "row_group": "excel_cols2",
            "tooltip": "支持多列，用 , 分隔。",
        }
    )
    genre_columns: str = field(
        default="类别", metadata={
            "label": "类别", "kind": "str", "width": 8,
            "row_group": "excel_cols2",
            "tooltip": "支持多列，用 , 分隔。",
        }
    )
    personal_comment_columns: str = field(
        default="个人评论", metadata={
            "label": "个人评论", "kind": "str", "width": 12,
            "row_group": "excel_cols2",
            "tooltip": "支持多列，用 , 分隔。",
        }
    )
    user_comment_columns: str = field(
        default="网友评论", metadata={
            "label": "网友评论", "kind": "str", "width": 12,
            "row_group": "excel_cols2",
            "tooltip": "支持多列，用 , 分隔。",
        }
    )

    # ---- NFO 选项 ----
    nfo_mode: str = field(
        default="每个视频生成同名 NFO", metadata={
            "label": "NFO 模式", "kind": "choice",
            "choices": _NFO_MODE_CHOICES,
            "row_group": "nfo_opts",
            "tooltip": "每个视频生成同名 NFO；或统一生成 movie.nfo；或两者都生成。",
        }
    )
    cover_naming: str = field(
        default="与视频同名", metadata={
            "label": "封面命名", "kind": "choice",
            "choices": _COVER_NAMING_CHOICES,
            "row_group": "nfo_opts",
            "tooltip": "把封面复制到电影文件夹时的命名方式。",
        }
    )
    copy_cover: bool = field(
        default=True, metadata={
            "label": "复制封面", "kind": "bool",
            "row_group": "nfo_switches",
        }
    )
    overwrite_existing: bool = field(
        default=True, metadata={
            "label": "覆盖已有 NFO", "kind": "bool",
            "row_group": "nfo_switches",
        }
    )

    # ---- 历史（隐藏）----
    recent_input_excels: list = field(
        default_factory=list, metadata={"hidden": True})
    recent_movie_roots: list = field(
        default_factory=list, metadata={"hidden": True})
    recent_cover_dirs: list = field(
        default_factory=list, metadata={"hidden": True})

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: Path) -> "JellyfinNfoConfig":
        if not path.exists():
            cfg = cls()
            try:
                cfg.save(path)
            except OSError:
                pass
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

        known = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}

        # 旧版本用的是英文 key，做一次迁移
        legacy_nfo = {
            "per_video": "每个视频生成同名 NFO",
            "movie_nfo": "只生成 movie.nfo",
            "both": "两种都生成",
        }
        legacy_cover = {
            "same_as_video": "与视频同名",
            "code": "用番号命名",
            "both": "两种都复制",
        }
        if filtered.get("nfo_mode") in legacy_nfo:
            filtered["nfo_mode"] = legacy_nfo[filtered["nfo_mode"]]
        if filtered.get("cover_naming") in legacy_cover:
            filtered["cover_naming"] = legacy_cover[filtered["cover_naming"]]

        return cls(**filtered)

    def save(self, path: Path) -> None:
        # 原子写：写到一半失败时，磁盘上还是原来那份可用配置
        text = json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2)
        write_via_temp(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
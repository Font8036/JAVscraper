"""Jellyfin NFO 插件自己的配置。"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JellyfinNfoConfig:
    # 路径
    input_excel: str = ""
    movie_root: str = ""
    cover_source_dir: str = ""

    # Excel 解析
    sheet_index: int = 1           # 第几个工作表（从 1 开始）
    header_row: int = 1            # 列名所在行

    code_column: str = "番号"                       # 单列
    cover_column: str = "封面"      # ← 新增
    title_column: str = "标题"                      # 单列
    actor_columns: str = "演员"                     # 多列，逗号分隔
    rating_columns: str = "评分"                    # 多列
    genre_columns: str = "类别"                     # 多列
    personal_comment_columns: str = "个人评论"      # 多列
    user_comment_columns: str = "网友评论"          # 多列

    # NFO 选项
    nfo_mode: str = "per_video"       # per_video / movie_nfo / both
    cover_naming: str = "both"        # same_as_video / code / both
    copy_cover: bool = True
    overwrite_existing: bool = False

    # 历史
    recent_input_excels: list = field(default_factory=list)
    recent_movie_roots: list = field(default_factory=list)
    recent_cover_dirs: list = field(default_factory=list)

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
        return cls(**filtered)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
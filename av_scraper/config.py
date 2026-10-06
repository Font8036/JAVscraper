"""配置的数据模型与 JSON 读写。

配置项用 dataclass + metadata 声明，GUI 会根据 metadata 自动生成控件。
新增一个配置项时，只需在下面的 dataclass 里加一行。
"""

from __future__ import annotations
import dataclasses
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .defaults import DEFAULT_ATTACHMENT_EXTENSIONS, DEFAULT_VIDEO_EXTENSIONS, DEFAULT_PREFIXES


@dataclass
class ScraperConfig:

    output_directory: str = field(
        default="",
        metadata={
            "label": "输出目录",
            "kind": "dir",
            "tooltip": "扫描结果（JSON / TXT / CSV）保存的位置。留空则自动保存到程序目录下的 log/ 里。一般留空即可。",
        },
    )
    output_filename: str = field(
        default="scraper_results.json",
        metadata={
            "label": "输出文件名", 
            "kind": "str",
            "tooltip": "扫描结果 JSON 的文件名。移动功能需要读取这份文件。一般不需要修改。",
        },
    )
    recursive_processing: bool = field(
        default=True,
        metadata={
            "label": "递归处理子文件夹",
            "kind": "bool",
            "row_group": "scraper_bools",     # ← 新增
        },
    )
    inherit_from_parent: bool = field(
        default=True,
        metadata={
            "label": "从父目录继承番号",
            "kind": "bool",
            "row_group": "scraper_bools",     # ← 新增
        },
    )
    separators: list[str] = field(
        default_factory=lambda: ["-", "_"],
        metadata={
            "label": "连接符",
            "kind": "list",
            "row_group": "scraper_lists",     # ← 新增
        },
    )
    video_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_VIDEO_EXTENSIONS),
        metadata={
            "label": "视频扩展名",
            "kind": "list",
            "row_group": "scraper_lists",
            "tooltip": "识别为“视频”的扩展名。\n"
                       "移动页的“附件跟随视频命名”依赖这里的分类。\n"
                       "全部清空则扫描器不匹配任何视频。",
        },
    )
    attachment_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_ATTACHMENT_EXTENSIONS),
        metadata={
            "label": "附件扩展名",
            "kind": "list",
            "row_group": "scraper_lists",
            "tooltip": "识别为“附件”的扩展名（字幕等）。\n"
                       "附件在移动时可选择跟随同目录视频命名。",
        },
    )
    known_alpha_prefixes: list[str] = field(
        default_factory=lambda: list(DEFAULT_PREFIXES),
        metadata={
            "label": "已知字母前缀", 
            "kind": "list",
            "big": True, 
            "row_group": "scraper_lists",
        },
    )
    recent_scan_dirs: list[str] = field(
        default_factory=list,
        metadata={"hidden": True},
    )


@dataclass
class ProcessorConfig:
    input_json: str = field(
        default="",
        metadata={"hidden": True},
    )
    target_directory: str = field(
        default="",
        metadata={"hidden": True},
    )
    move_to_extracted_folder: bool = field(
        default=True,
        metadata={
            "label": "移动到提取名子文件夹", 
            "kind": "bool",
            "row_group": "processor_naming",   # ← 新增
            "tooltip": "勾上后，每个番号建一个子文件夹，文件放进去。\n"
                "取消则所有文件平铺到目标目录。\n考虑到同一个番号可能对应多个视频文件，且还可能会有字幕文件等，建议勾上。",
        },
    )
    video_naming: str = field(
        default="保留原文件名",
        metadata={
            "label": "视频命名方式",
            "kind": "choice",
            "choices": ["保留原文件名", "按提取码命名"],
            "row_group": "processor_naming",   # ← 和附件那项排一行
            "tooltip": "保留原文件名 — 不动视频名（默认）\n"
                       "按提取码命名 — 视频改为「提取码.扩展名」",
        },
    )
    attachment_naming: str = field(
        default="保留原文件名",
        metadata={
            "label": "附件命名方式",
            "kind": "choice",
            "choices": ["保留原文件名", "按提取码命名", "跟随视频命名"],
            "row_group": "processor_naming",   # ← 和附件那项排一行
            "tooltip": "保留原文件名 — 不动附件名（默认）\n"
                       "按提取码命名 — 附件改为「提取码.扩展名」\n"
                       "跟随视频命名 — 附件与同目录视频同名。\n"
                       "  同目录需恰好一个视频；否则退回保留原名。\n"
                       "  Jellyfin 靠文件名匹配字幕，跟随命名可自动关联。",
        },
    )
    existing_file_handling: str = field(
        default="重命名",
        metadata={
            "label": "文件冲突处理",
            "kind": "choice",
            "choices": ["跳过", "覆盖", "重命名"],
            "tooltip": "目标目录已有同名文件时：\n"
                       "  跳过 — 不处理该文件\n"
                       "  覆盖 — 删除已存在的目标文件后移入 ⚠\n"
                       "  重命名 — 目标名加 _01、_02 后缀",
        },
    )
    recent_target_dirs: list[str] = field(
        default_factory=list,
        metadata={"hidden": True},
    )

def _filter_fields(cls, data) -> dict:
    """从 dict 里挑出 cls 认识的字段。非 dict 输入或未知字段都安全忽略。"""
    if not isinstance(data, dict):
        return {}
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in known}

def _coerce_choices(cls, data: dict) -> dict:
    """把 choice 字段的非法值替换为默认值。

    兼容用户手改 config.json，以及旧枚举值升级后的情形。
    """
    if not isinstance(data, dict):
        return data
    result = dict(data)
    for f in dataclasses.fields(cls):
        if f.name not in result:
            continue
        choices = f.metadata.get("choices")
        if not choices:
            continue
        if result[f.name] not in choices and f.default is not dataclasses.MISSING:
            result[f.name] = f.default
    return result

def _migrate_scraper_config(data: dict) -> dict:
    """把 0.4.x 的 supported_extensions 拆成 video / attachment 两组。

    只在新字段都不存在时执行一次。不认识的自定义扩展名归到视频，
    保持原有行为（不认识的扩展名以前也会被扫描）。
    """
    if not isinstance(data, dict):
        return data
    if "video_extensions" in data or "attachment_extensions" in data:
        return data
    old = data.get("supported_extensions")
    if not isinstance(old, list):
        return data

    video_set = {e.lower() for e in DEFAULT_VIDEO_EXTENSIONS}
    attach_set = {e.lower() for e in DEFAULT_ATTACHMENT_EXTENSIONS}

    videos, attachments = [], []
    for ext in old:
        e = str(ext).lower()
        if e in attach_set:
            attachments.append(e)
        elif e in video_set:
            videos.append(e)
        else:
            videos.append(e)        # 未知扩展名按视频处理

    new_data = dict(data)
    new_data.pop("supported_extensions", None)
    new_data["video_extensions"] = videos
    new_data["attachment_extensions"] = attachments
    return new_data

@dataclass
class AppConfig:
    scraper: ScraperConfig = field(
        default_factory=ScraperConfig,
        metadata={"hidden": True},
    )
    processor: ProcessorConfig = field(
        default_factory=ProcessorConfig,
        metadata={"hidden": True},
    )
    confirm_on_close: bool = field(
        default=True,
        metadata={
            "label": "关闭时二次确认", 
            "kind": "bool",
        },
    )
    max_recent_dirs: int = field(
        default=5,
        metadata={
            "label": "历史目录上限", 
            "kind": "int",
        },
    )

    # ===== 新增 =====
    log_height: int = field(
        default=8,
        metadata={
            "label": "日志区高度（行）", "kind": "int",
            "row_group": "界面布局", 
            "tooltip": "各页面日志区显示的行数。\n日志区高度不会随窗口大小自动调整。",
        },
    )
    table_height: int = field(
        default=10,
        metadata={
            "label": "表格区高度（行）", "kind": "int",
            "row_group": "界面布局", 
            "tooltip": "各页面表格默认显示的行数。\n表格高度会随窗口大小自动调整，但不会小于这个值。",
        },
    )
    ui_scale: int = field(
        default=100,
        metadata={
            "label": "界面缩放(%)",
            "row_group": "界面布局", 
            "kind": "int",
            "tooltip": "100 = 系统默认大小；150 = 放大 50%。\n"
                    "范围建议 75~200，改动后需重启程序生效。",
        },
    )

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        
        scraper_data = _coerce_choices(
            ScraperConfig, _migrate_scraper_config(data.get("scraper")))
        processor_data = _coerce_choices(ProcessorConfig, data.get("processor"))

        kwargs = {
            "scraper": ScraperConfig(**_filter_fields(ScraperConfig, scraper_data)),
            "processor": ProcessorConfig(
                **_filter_fields(ProcessorConfig, processor_data)),
        }
        for f in dataclasses.fields(cls):
            if f.name in kwargs:
                continue
            if f.name in data:
                kwargs[f.name] = data[f.name]

        return cls(**kwargs)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
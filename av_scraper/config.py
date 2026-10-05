"""配置的数据模型与 JSON 读写。

配置项用 dataclass + metadata 声明，GUI 会根据 metadata 自动生成控件。
新增一个配置项时，只需在下面的 dataclass 里加一行。
"""

from __future__ import annotations
import dataclasses
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .defaults import DEFAULT_EXTENSIONS, DEFAULT_PREFIXES


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
    supported_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXTENSIONS),
        metadata={
            "label": "支持的扩展名",
            "kind": "list", 
            "row_group": "scraper_lists",
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
            "row_group": "processor_bools",   # ← 新增
            "tooltip": "勾上后，每个番号建一个子文件夹，文件放进去。\n"
                "取消则所有文件平铺到目标目录。\n考虑到同一个番号可能对应多个视频文件，且还可能会有字幕文件等，建议勾上。",
        },
    )
    enable_rename: bool = field(
        default=False,
        metadata={
            "label": "将文件重命名为提取名", 
            "kind": "bool",
            "row_group": "processor_bools",   # ← 新增
            "tooltip": "勾上后，用提取到的番号作为文件名（保留原扩展名）。\n"
                "取消则保持原文件名。",
        },
    )
    existing_file_handling: str = field(
        default="rename",
        metadata={
            "label": "文件冲突处理",
            "kind": "choice",
            "choices": ["skip", "overwrite", "rename"],
            "tooltip": "目标目录已有同名文件时：\n"
                       "  skip — 跳过\n"
                       "  overwrite — 覆盖\n"
                       "  rename — 自动加 _01、_02 后缀",
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
            "tooltip": "各页面日志区显示的行数。",
        },
    )
    table_height: int = field(
        default=6,
        metadata={
            "label": "表格区高度（行）", "kind": "int",
            "tooltip": "各页面表格默认显示的行数。",
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
        kwargs = {
            "scraper": ScraperConfig(
                **_filter_fields(ScraperConfig, data.get("scraper"))),
            "processor": ProcessorConfig(
                **_filter_fields(ProcessorConfig, data.get("processor"))),
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
"""配置的数据模型与 JSON 读写。

配置项用 dataclass + metadata 声明，GUI 会根据 metadata 自动生成控件。
新增一个配置项时，只需在下面的 dataclass 里加一行。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .defaults import DEFAULT_EXTENSIONS, DEFAULT_PREFIXES


@dataclass
class ScraperConfig:
    default_directory: str = field(
        default="",
        metadata={"label": "默认扫描目录", "kind": "dir"},
    )
    output_directory: str = field(
        default="",
        metadata={"label": "输出目录", "kind": "dir"},
    )
    output_filename: str = field(
        default="scraper_results.json",
        metadata={"label": "输出文件名", "kind": "str"},
    )
    recursive_processing: bool = field(
        default=True,
        metadata={"label": "递归处理子文件夹", "kind": "bool"},
    )
    separators: list[str] = field(
        default_factory=lambda: ["-", "_"],
        metadata={"label": "连接符", "kind": "list"},
    )
    supported_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXTENSIONS),
        metadata={"label": "支持的扩展名", "kind": "list"},
    )
    known_alpha_prefixes: list[str] = field(
        default_factory=lambda: list(DEFAULT_PREFIXES),
        metadata={"label": "已知字母前缀", "kind": "list", "big": True},
    )
    max_recent_dirs: int = field(
        default=5,
        metadata={"label": "历史目录上限", "kind": "int"},
    )
    recent_scan_dirs: list[str] = field(
        default_factory=list,
        metadata={"label": "最近扫描目录", "kind": "list", "hidden": True},
    )


@dataclass
class ProcessorConfig:
    input_json: str = field(
        default="",
        metadata={"label": "输入 JSON", "kind": "file"},
    )
    target_directory: str = field(
        default="",
        metadata={"label": "目标目录", "kind": "dir"},
    )
    move_to_extracted_folder: bool = field(
        default=True,
        metadata={"label": "移动到提取名子文件夹", "kind": "bool"},
    )
    enable_rename: bool = field(
        default=False,
        metadata={"label": "启用重命名", "kind": "bool"},
    )
    existing_file_handling: str = field(
        default="rename",
        metadata={
            "label": "文件冲突处理",
            "kind": "choice",
            "choices": ["skip", "overwrite", "rename"],
        },
    )
    recent_target_dirs: list[str] = field(
        default_factory=list,
        metadata={"label": "最近目标目录", "kind": "list", "hidden": True},
    )


@dataclass
class AppConfig:
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    processor: ProcessorConfig = field(default_factory=ProcessorConfig)
    confirm_on_close: bool = field(
        default=True,
        metadata={"label": "关闭时二次确认", "kind": "bool"},
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
        return cls(
            scraper=ScraperConfig(**data.get("scraper", {})),
            processor=ProcessorConfig(**data.get("processor", {})),
            confirm_on_close=data.get("confirm_on_close", True),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
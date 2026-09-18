"""JavDB 插件自己的配置。

与核心的 config.json 完全隔离，放在插件目录里，
插件可以带着配置一起搬走。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JavdbConfig:
    # 路径
    input_excel: str = ""
    cover_dir: str = ""
    output_csv: str = ""
    output_excel: str = ""
    state_file: str = ""

    # 运行参数
    show_browser: bool = True
    browser_channel: str = "msedge"
    request_delay: float = 1.0
    page_timeout: int = 30000            # 毫秒
    max_retries: int = 1

    # 行为开关
    skip_existing_covers: bool = True
    save_state_on_exit: bool = True

    @classmethod
    def load(cls, path: Path) -> "JavdbConfig":
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
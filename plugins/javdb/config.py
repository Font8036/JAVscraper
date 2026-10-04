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
    base_url: str = "https://javdb.com/"
    show_browser: bool = True
    browser_channel: str = "msedge"
    request_delay: float = 1.0
    page_timeout: int = 5000            # 毫秒
    max_retries: int = 1

    # 行为开关
    skip_existing_covers: bool = True
    save_state_on_exit: bool = True
    
    # 完成提示
    notify_sound: bool = True
    notify_toast: bool = False
    @classmethod
    def load(cls, path: Path) -> "JavdbConfig":
        if not path.exists():
            cfg = cls()
            cfg.base_url = normalize_base_url(cfg.base_url) 
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
        cfg = cls(**filtered)
        cfg.base_url = normalize_base_url(cfg.base_url)       # ← 新增
        return cfg

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

def normalize_base_url(url: str) -> str:
    """把用户输入的站点地址规范化为带协议、带结尾斜杠的形式。"""
    url = (url or "").strip()
    if not url:
        return "https://javdb.com/"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    return url
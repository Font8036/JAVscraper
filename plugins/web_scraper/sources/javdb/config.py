"""JavDB 源的配置。"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path


def normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "https://javdb.com/"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    return url


@dataclass
class JavdbSourceConfig:
    # ---- 站点 ----
    base_url: str = field(
        default="https://javdb.com/",
        metadata={"label": "站点地址", "kind": "str",
                  "tooltip": "爬取目标的站点地址。"},
    )
    # ---- 路径 ----
    input_excel: str = field(default="", metadata={
        "label": "输入 Excel", "kind": "file", "ext": ".xlsx",
        "tooltip": "待爬取的番号清单，第一列一行一个。",
    })
    cover_dir: str = field(default="", metadata={
        "label": "封面目录", "kind": "dir",
        "tooltip": "下载的封面图片保存到哪里。",
    })
    output_csv: str = field(default="", metadata={
        "label": "输出 CSV", "kind": "save", "ext": ".csv",
        "tooltip": "爬取结果的中间文件，生成 Excel 时会读它。",
    })
    output_excel: str = field(default="", metadata={
        "label": "输出 Excel", "kind": "save", "ext": ".xlsx",
        "tooltip": "最终 Excel 报告的保存路径。",
    })
    state_file: str = field(default="", metadata={
        "label": "登录状态", "kind": "save", "ext": ".json",
        "tooltip": "javdb 登录状态保存路径。",
    })
    # ---- 参数 ----
    show_browser: bool = field(default=True, metadata={
        "label": "显示浏览器窗口", "kind": "bool",
        "row_group": "browser_opts",
    })
    browser_channel: str = field(default="msedge", metadata={
        "label": "浏览器", "kind": "choice",
        "choices": ["msedge", "chrome", "chromium"],
        "row_group": "browser_opts",
    })
    request_delay: float = field(default=1.0, metadata={
        "label": "请求间隔(秒)", "kind": "float",
        "row_group": "params_num",
    })
    page_timeout: int = field(default=60, metadata={
        "label": "超时(秒)", "kind": "int",
        "row_group": "params_num",
    })
    max_retries: int = field(default=2, metadata={
        "label": "重试次数", "kind": "int",
        "row_group": "params_num",
    })
    # ---- 开关 ----
    skip_existing_covers: bool = field(default=False, metadata={
        "label": "跳过已存在的封面", "kind": "bool",
        "row_group": "switches",
    })
    save_state_on_exit: bool = field(default=False, metadata={
        "label": "退出时保存登录状态", "kind": "bool",
        "row_group": "switches",
    })
    notify_sound: bool = field(default=True, metadata={
        "label": "完成时响铃", "kind": "bool",
        "row_group": "switches",
    })
    notify_toast: bool = field(default=False, metadata={
        "label": "完成时发系统通知", "kind": "bool",
        "row_group": "switches",
    })
    # ---- 历史 ----
    recent_input_excels: list = field(
        default_factory=list, metadata={"hidden": True})
    recent_cover_dirs: list = field(
        default_factory=list, metadata={"hidden": True})
    recent_state_files: list = field(
        default_factory=list, metadata={"hidden": True})

    @property
    def page_timeout_ms(self) -> int:
        return max(1, int(self.page_timeout)) * 1000

    @classmethod
    def from_dict(cls, data: dict) -> "JavdbSourceConfig":
        known = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = cls(**filtered)
        cfg.base_url = normalize_base_url(cfg.base_url)
        # 老版本兼容：page_timeout 曾经存的是毫秒
        if cfg.page_timeout >= 1000:
            cfg.page_timeout = cfg.page_timeout // 1000
        return cfg

    @classmethod
    def load(cls, path: Path) -> "JavdbSourceConfig":
        """独立加载（用于旧版迁移或直接测试）。"""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
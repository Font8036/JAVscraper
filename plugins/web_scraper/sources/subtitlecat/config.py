"""字幕猫源的配置。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


def normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "https://www.subtitlecat.com/"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not url.endswith("/"):
        url += "/"
    return url


@dataclass
class SubtitleCatConfig:
    # ---- 站点 ----
    base_url: str = field(
        default="https://www.subtitlecat.com/",
        metadata={"label": "站点地址", "kind": "str",
                  "tooltip": "字幕猫站点地址。"},
    )

    # ---- 路径 ----
    input_excel: str = field(default="", metadata={
        "label": "输入 Excel", "kind": "file", "ext": ".xlsx",
        "tooltip": "番号清单，第一列一行一个，无需表头。",
    })
    target_dir: str = field(default="", metadata={
        "label": "目标目录", "kind": "dir",
        "tooltip": "下载的字幕文件保存到哪里（平铺）。",
    })
    output_excel: str = field(default="", metadata={
        "label": "输出 Excel", "kind": "save", "ext": ".xlsx",
        "tooltip": "爬取结束后自动生成的结果表格。",
    })
    state_file: str = field(default="", metadata={
        "label": "登录状态", "kind": "save", "ext": ".json",
        "tooltip": "字幕猫登录状态保存路径。",
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
    page_timeout: int = field(default=10, metadata={
        "label": "超时(秒)", "kind": "int",
        "row_group": "params_num",
    })
    max_retries: int = field(default=1, metadata={
        "label": "重试次数", "kind": "int",
        "row_group": "params_num",
    })
    translate_timeout: int = field(default=30, metadata={
        "label": "等待翻译(秒)", "kind": "int",
        "row_group": "translate_params",
        "tooltip": "点击「翻译」后，等待网站生成下载链接的最长时间。\n超时后如果还没生成下载链接，则记为翻译超时。",
    })
    round_delay: float = field(default=3.0, metadata={
        "label": "轮间等待(秒)", "kind": "float",
        "row_group": "translate_params",
        "tooltip": "重试等待翻译的番号时，每轮之间的等待时间。",
    })

    # ---- 开关 ----
    save_state_on_exit: bool = field(default=True, metadata={
        "label": "退出时保存登录状态", "kind": "bool",
        "row_group": "switches",
    })
    notify_sound: bool = field(default=True, metadata={
        "label": "完成时响铃", "kind": "bool",
        "row_group": "switches",
    })
    notify_toast: bool = field(default=True, metadata={
        "label": "完成时发系统通知", "kind": "bool",
        "row_group": "switches",
    })

    # ---- 历史 ----
    recent_input_excels: list = field(
        default_factory=list, metadata={"hidden": True})
    recent_target_dirs: list = field(
        default_factory=list, metadata={"hidden": True})
    recent_state_files: list = field(
        default_factory=list, metadata={"hidden": True})

    @property
    def page_timeout_ms(self) -> int:
        return max(1, int(self.page_timeout)) * 1000

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleCatConfig":
        known = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = cls(**filtered)
        cfg.base_url = normalize_base_url(cfg.base_url)
        if cfg.page_timeout >= 1000:
            cfg.page_timeout = cfg.page_timeout // 1000
        return cfg
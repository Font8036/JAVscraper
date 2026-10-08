"""av_scraper.config 的单元测试。

重点在「加载配置时程序替你改了什么，有没有如实报告出来」：

  - 首次运行：按默认值新建文件
  - 旧字段迁移（supported_extensions → video / attachment）
  - 非法选项回退到默认值
  - 文件损坏或结构不对：按默认值启动，但**不能**动原文件

这些说明会由 GUI 弹窗告知用户，所以文案里要能指明是哪一项、变成了什么。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from av_scraper.config import (
    AppConfig,
    ProcessorConfig,
    ScraperConfig,
    _coerce_choices,
    _filter_fields,
    _migrate_scraper_config,
)


@pytest.fixture
def cfg_dir():
    base = Path(tempfile.gettempdir()) / f"javscraper_cfg_{uuid.uuid4().hex}"
    base.mkdir()
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def write_config(path: Path, data) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_load_creates_default_config_when_missing(cfg_dir):
    path = cfg_dir / "config.json"
    cfg, notices = AppConfig.load_with_notices(path)

    assert path.exists()                       # 首次运行会落盘
    assert notices == []                       # 新建不是"改动"，由欢迎提示负责
    assert cfg.scraper.video_extensions        # 有默认值
    assert cfg.processor.existing_file_handling == "重命名"


def test_load_wrapper_returns_config_only(cfg_dir):
    """load() 保持老签名：只要配置对象。"""
    path = write_config(cfg_dir / "config.json", {})
    cfg = AppConfig.load(path)
    assert isinstance(cfg, AppConfig)


def test_save_and_load_round_trip_has_no_notices(cfg_dir):
    path = cfg_dir / "config.json"
    original = AppConfig()
    original.scraper.separators = ["-", "_", "."]
    original.processor.target_directory = r"D:\整理后"
    original.processor.video_naming = "按提取码命名"
    original.max_recent_dirs = 7
    original.save(path)

    cfg, notices = AppConfig.load_with_notices(path)

    assert notices == []
    assert cfg.scraper.separators == ["-", "_", "."]
    assert cfg.processor.target_directory == r"D:\整理后"
    assert cfg.processor.video_naming == "按提取码命名"
    assert cfg.max_recent_dirs == 7


def test_save_writes_readable_utf8(cfg_dir):
    path = cfg_dir / "config.json"
    AppConfig().save(path)
    assert "保留原文件名" in path.read_text(encoding="utf-8")   # 中文不转义


def test_unknown_keys_are_ignored(cfg_dir):
    path = write_config(cfg_dir / "config.json", {
        "scraper": {"video_extensions": [".mp4"], "已经删掉的字段": 1},
        "processor": {"target_directory": r"D:\x"},
        "未来才会有的字段": "whatever",
    })
    cfg, notices = AppConfig.load_with_notices(path)
    assert notices == []
    assert cfg.scraper.video_extensions == [".mp4"]
    assert cfg.processor.target_directory == r"D:\x"


# ---------------------------------------------------------------------------
# 非法选项回退（要提示用户）
# ---------------------------------------------------------------------------
def test_load_reports_coerced_choice(cfg_dir):
    path = write_config(cfg_dir / "config.json", {
        "processor": {"existing_file_handling": "瞎写的值"},
    })

    cfg, notices = AppConfig.load_with_notices(path)

    assert cfg.processor.existing_file_handling == "重命名"      # 回退到默认
    assert len(notices) == 1
    assert "文件冲突处理" in notices[0]                          # 指出是哪一项
    assert "瞎写的值" in notices[0] and "重命名" in notices[0]    # 旧值 → 新值


def test_load_reports_every_coerced_choice(cfg_dir):
    path = write_config(cfg_dir / "config.json", {
        "processor": {"video_naming": "A", "attachment_naming": "B"},
    })
    _, notices = AppConfig.load_with_notices(path)
    assert len(notices) == 2


def test_valid_choices_produce_no_notice(cfg_dir):
    path = write_config(cfg_dir / "config.json", {
        "processor": {
            "video_naming": "按提取码命名",
            "attachment_naming": "跟随视频命名",
            "existing_file_handling": "覆盖",
        },
    })
    _, notices = AppConfig.load_with_notices(path)
    assert notices == []


# ---------------------------------------------------------------------------
# 旧字段迁移（要提示用户）
# ---------------------------------------------------------------------------
def test_load_migrates_legacy_extensions_and_reports(cfg_dir):
    path = write_config(cfg_dir / "config.json", {
        "scraper": {"supported_extensions": [".mp4", ".srt"]},
    })

    cfg, notices = AppConfig.load_with_notices(path)

    assert cfg.scraper.video_extensions == [".mp4"]
    assert cfg.scraper.attachment_extensions == [".srt"]
    assert len(notices) == 1
    assert "支持扩展名" in notices[0]


def test_migration_notice_absent_when_new_fields_present(cfg_dir):
    path = write_config(cfg_dir / "config.json", {
        "scraper": {
            "supported_extensions": [".mp4", ".srt"],       # 新旧都有 → 不迁移
            "video_extensions": [".mkv"],
            "attachment_extensions": [".ass"],
        },
    })
    cfg, notices = AppConfig.load_with_notices(path)
    assert notices == []
    assert cfg.scraper.video_extensions == [".mkv"]
    assert cfg.scraper.attachment_extensions == [".ass"]


def test_migrate_keeps_unknown_extensions_as_video():
    migrated = _migrate_scraper_config(
        {"supported_extensions": [".mp4", ".srt", ".xyz"]}
    )
    assert migrated["video_extensions"] == [".mp4", ".xyz"]
    assert migrated["attachment_extensions"] == [".srt"]
    assert "supported_extensions" not in migrated


def test_migrate_is_noop_without_legacy_field():
    data = {"video_extensions": [".mp4"]}
    assert _migrate_scraper_config(data) == data


# ---------------------------------------------------------------------------
# 文件损坏：用默认值启动，但不能动原文件
# ---------------------------------------------------------------------------
def test_load_reports_broken_json_and_keeps_file(cfg_dir):
    path = cfg_dir / "config.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    cfg, notices = AppConfig.load_with_notices(path)

    assert len(notices) == 1
    assert "读取失败" in notices[0]
    assert path.read_text(encoding="utf-8") == "{ 这不是 JSON"    # 原文件原封不动
    assert cfg.processor.existing_file_handling == "重命名"        # 默认值可用


def test_load_reports_non_object_json(cfg_dir):
    path = write_config(cfg_dir / "config.json", ["不是对象"])

    cfg, notices = AppConfig.load_with_notices(path)

    assert len(notices) == 1
    assert "结构" in notices[0]
    assert cfg.max_recent_dirs == AppConfig().max_recent_dirs


# ---------------------------------------------------------------------------
# 两个小工具
# ---------------------------------------------------------------------------
def test_filter_fields_ignores_unknown_and_non_dict():
    assert _filter_fields(ScraperConfig, {"separators": ["-"], "未知": 1}) == {
        "separators": ["-"]
    }
    assert _filter_fields(ScraperConfig, "不是字典") == {}


def test_coerce_choices_only_touches_invalid_choice_fields():
    data = {
        "existing_file_handling": "跳过",       # 合法 → 不动
        "video_naming": "不合法",                                  # 非法 → 回默认
        "target_directory": "任意字符串",                           # 非 choice → 不动
    }
    result = _coerce_choices(ProcessorConfig, data)
    assert result["existing_file_handling"] == "跳过"
    assert result["video_naming"] == ProcessorConfig().video_naming
    assert result["target_directory"] == "任意字符串"

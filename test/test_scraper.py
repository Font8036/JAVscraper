"""av_scraper.scraper 的单元测试。

覆盖三块：
  1. CodeExtractor.extract —— 番号提取规则与边界
  2. 文件分类             —— is_video / is_attachment / is_supported
  3. scan_directory       —— 遍历范围、父目录继承、进度回调

关于临时目录：这里刻意不用 pytest 的 tmp_path —— 它和 tempfile.mkdtemp 一样会以
0o700 建目录，在部分受限环境（例如带写入限制的沙箱）里，这种目录连列目录都会被
拒绝。用默认权限建目录，受限环境和普通终端都能跑。
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from av_scraper.config import ScraperConfig
from av_scraper.defaults import (
    DEFAULT_ATTACHMENT_EXTENSIONS,
    DEFAULT_PREFIXES,
    DEFAULT_VIDEO_EXTENSIONS,
)
from av_scraper.scraper import CodeExtractor


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------
def make_config(**overrides) -> ScraperConfig:
    """构造可控配置：只用少量前缀，避免默认的 400+ 前缀干扰断言。"""
    values = dict(
        separators=["-", "_"],
        known_alpha_prefixes=["ABP", "SSIS", "IPX", "IPZZ"],
        video_extensions=[".mp4", ".mkv"],
        attachment_extensions=[".srt"],
        recursive_processing=True,
        inherit_from_parent=True,
    )
    values.update(overrides)
    return ScraperConfig(**values)


@pytest.fixture
def extractor() -> CodeExtractor:
    return CodeExtractor(make_config())


@pytest.fixture
def scan_root():
    """一个可读写的临时目录，退出时清理。"""
    root = Path(tempfile.gettempdir()) / f"javscraper_scan_{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def make_file(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. 番号提取：字母前缀
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "filename, expected",
    [
        ("ABP-123.mp4", "ABP-123"),
        ("ABP_123.mp4", "ABP-123"),
        ("abp-123.mp4", "ABP-123"),                # 大小写不敏感，输出统一大写
        ("SSIS-456 某某标题.mkv", "SSIS-456"),      # 番号后面还有标题
        ("my-abp-123-file.mp4", "ABP-123"),        # 番号夹在中间
        ("[ABP-123] 标题.mp4", "ABP-123"),
        ("ABP-12.mp4", "ABP-12"),                  # 2 位数字
        ("ABP-1234.mp4", "ABP-1234"),              # 4 位数字
        ("IPZZ-123.mp4", "IPZZ-123"),              # 长前缀优先
        ("ABP-123", "ABP-123"),                    # 没有扩展名也能提取
        # 以下按"宁可漏抓，不可误抓"的设计，应当拒绝：
        ("ABP-1.mp4", None),                       # 数字少于 2 位
        ("ABP-12345.mp4", None),                   # 数字多于 4 位
        ("ABP123.mp4", None),                      # 缺连接符
        ("XYZ-123.mp4", None),                     # 前缀不在白名单里
        ("随机文件名.mp4", None),
    ],
)
def test_extract_alpha_prefix(extractor, filename, expected):
    assert extractor.extract(filename) == expected


def test_extract_uses_configured_separators():
    ext = CodeExtractor(make_config(separators=["."]))
    assert ext.extract("ABP.123.mp4") == "ABP-123"
    assert ext.extract("ABP-123.mp4") is None      # "-" 已不在连接符里


# ---------------------------------------------------------------------------
# 2. 番号提取：FC2 与数字前缀
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "filename, expected",
    [
        # 连接符可有可无：实际文件里这些写法都常见
        ("FC2PPV1234567.mp4", "FC2-1234567"),
        ("FC2PPV-1234567.mp4", "FC2-1234567"),
        ("FC2PPV_1234567.mp4", "FC2-1234567"),
        ("FC2-PPV-1234567.mp4", "FC2-1234567"),
        ("FC2_PPV_1234567.mp4", "FC2-1234567"),
        ("FC2-PPV1234567.mp4", "FC2-1234567"),
        ("[FC2PPV1234567] 标题.mp4", "FC2-1234567"),
        ("hhd800.com@FC2PPV1234567.mp4", "FC2-1234567"),
        # 裸 FC2 + 数字仍要求连接符，避免把无关编号误认
        ("FC2-1234567.mp4", "FC2-1234567"),
        ("FC2_1234567.mp4", "FC2-1234567"),
        ("FC2-123456.mp4", "FC2-123456"),          # 6 位
        # 位数不对、或缺连接符：一律拒绝，绝不截断
        ("FC2-12345.mp4", None),                   # 少于 6 位
        ("FC2-12345678.mp4", None),                # 多于 7 位
        ("FC2PPV12345678.mp4", None),
        ("FC21234567.mp4", None),
    ],
)
def test_extract_fc2(extractor, filename, expected):
    assert extractor.extract(filename) == expected


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("123456-789.mp4", "123456-789"),
        ("123456_789.mp4", "123456-789"),
        ("12345-789.mp4", None),                   # 少于 6 位
        ("12345-7890.mp4", None),                  # 后段不是 3 位
        ("1234567-789.mp4", None),                 # 7 位前缀：不得从第 2 位截出 234567-789
        ("12345678-123.mp4", None),
    ],
)
def test_extract_digital_prefix(extractor, filename, expected):
    assert extractor.extract(filename) == expected


def test_extract_priority_fc2_before_alpha(extractor):
    """同一个文件名里既有 FC2 又有字母前缀时，FC2 优先。"""
    assert extractor.extract("FC2-1234567-SSIS-999.mp4") == "FC2-1234567"


# ---------------------------------------------------------------------------
# 4. 文件分类
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("filename", ["a.mp4", "a.MP4", "a.mkv", "a.MKV"])
def test_is_video(extractor, filename):
    assert extractor.is_video(filename) is True


@pytest.mark.parametrize("filename", ["a.srt", "a.SRT"])
def test_is_attachment(extractor, filename):
    assert extractor.is_attachment(filename) is True


@pytest.mark.parametrize("filename", ["a.mp4", "a.srt"])
def test_is_supported_covers_video_and_attachment(extractor, filename):
    assert extractor.is_supported(filename) is True


@pytest.mark.parametrize("filename", ["a.txt", "a", "a.mp4.bak"])
def test_unsupported_extensions(extractor, filename):
    assert extractor.is_supported(filename) is False
    assert extractor.is_video(filename) is False
    assert extractor.is_attachment(filename) is False


def test_default_config_classifies_disc_images_as_video():
    """defaults.py 有意把整盘镜像归为视频（内容本体，不是附件）。"""
    ext = CodeExtractor(ScraperConfig())
    assert ext.is_video("disc.iso") is True
    assert ext.is_video("disc.img") is True
    assert ext.is_attachment("sub.srt") is True
    assert ext.is_supported("readme.txt") is False


def test_defaults_are_populated():
    assert ".mp4" in DEFAULT_VIDEO_EXTENSIONS
    assert ".srt" in DEFAULT_ATTACHMENT_EXTENSIONS
    assert len(DEFAULT_PREFIXES) > 100
    for prefix in ("ABP", "SSIS", "IPX"):
        assert prefix in DEFAULT_PREFIXES


# ---------------------------------------------------------------------------
# 5. scan_directory
# ---------------------------------------------------------------------------
def test_scan_collects_supported_files_only(scan_root):
    make_file(scan_root / "ABP-123.mp4")
    make_file(scan_root / "SSIS-456.mkv")
    make_file(scan_root / "ABP-123.srt")          # 附件也算支持
    make_file(scan_root / "notes.txt")            # 不支持
    results = CodeExtractor(make_config()).scan_directory(scan_root)
    assert {r.filename for r in results} == {"ABP-123.mp4", "SSIS-456.mkv", "ABP-123.srt"}


def test_scan_accepts_uppercase_extensions(scan_root):
    make_file(scan_root / "ABP-123.MP4")
    (result,) = CodeExtractor(make_config()).scan_directory(scan_root)
    assert result.extracted_code == "ABP-123"


def test_scan_recursive_flag(scan_root):
    make_file(scan_root / "ABP-123.mp4")
    make_file(scan_root / "sub" / "SSIS-456.mp4")
    ext = CodeExtractor(make_config())
    assert {r.filename for r in ext.scan_directory(scan_root, recursive=False)} == {"ABP-123.mp4"}
    assert {r.filename for r in ext.scan_directory(scan_root, recursive=True)} == {
        "ABP-123.mp4",
        "SSIS-456.mp4",
    }


def test_scan_respects_config_recursive_flag(scan_root):
    make_file(scan_root / "sub" / "SSIS-456.mp4")
    ext = CodeExtractor(make_config(recursive_processing=False))
    assert ext.scan_directory(scan_root) == []


def test_scan_ignores_directories_named_like_videos(scan_root):
    (scan_root / "SSIS-999.mp4").mkdir()          # 名字像视频，其实是目录
    make_file(scan_root / "SSIS-999.mp4" / "ABP-123.mp4")
    ext = CodeExtractor(make_config())
    # 非递归：这个"视频目录"本身不能被当成文件，它里面的文件也不该被扫到
    assert ext.scan_directory(scan_root, recursive=False) == []
    # 递归：只扫到里面的真实文件
    results = ext.scan_directory(scan_root, recursive=True)
    assert {r.filename for r in results} == {"ABP-123.mp4"}


def test_scan_marks_extracted_and_original(scan_root):
    make_file(scan_root / "ABP-123.mp4")
    make_file(scan_root / "random.mp4")
    results = {r.filename: r for r in CodeExtractor(make_config()).scan_directory(scan_root)}

    ok = results["ABP-123.mp4"]
    assert (ok.extracted_code, ok.status, ok.is_extracted, ok.inherited) == (
        "ABP-123", "extracted", True, False,
    )

    bad = results["random.mp4"]
    assert (bad.extracted_code, bad.status, bad.is_extracted) == ("random.mp4", "original", False)


def test_scan_inherits_code_from_parent_directory(scan_root):
    make_file(scan_root / "ABP-123" / "random.mp4")
    (result,) = CodeExtractor(make_config()).scan_directory(scan_root)
    assert result.extracted_code == "ABP-123"
    assert result.inherited is True
    assert result.status == "extracted"


def test_scan_inheritance_can_be_disabled(scan_root):
    make_file(scan_root / "ABP-123" / "random.mp4")
    (result,) = CodeExtractor(make_config(inherit_from_parent=False)).scan_directory(scan_root)
    assert result.extracted_code == "random.mp4"
    assert result.inherited is False
    assert result.status == "original"


def test_scan_reports_total_and_progress(scan_root):
    make_file(scan_root / "ABP-123.mp4")
    make_file(scan_root / "SSIS-456.mp4")
    totals: list[int] = []
    seen = []
    results = CodeExtractor(make_config()).scan_directory(
        scan_root, on_total=totals.append, progress=seen.append
    )
    assert totals == [2]                  # 只回调一次，是候选文件总数
    assert seen == results                # 逐个回调，顺序与返回值一致


def test_scan_records_file_size_and_path(scan_root):
    make_file(scan_root / "ABP-123.mp4", "12345")
    (result,) = CodeExtractor(make_config()).scan_directory(scan_root)
    assert result.file_size == 5
    assert Path(result.file_path).name == "ABP-123.mp4"


def test_scan_missing_directory_raises(scan_root):
    with pytest.raises(FileNotFoundError):
        CodeExtractor(make_config()).scan_directory(scan_root / "not-here")

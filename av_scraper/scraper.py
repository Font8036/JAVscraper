"""从文件名中提取标准化代码。

设计目标：宁可漏抓，不可误抓。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ScraperConfig

logger = logging.getLogger(__name__)

# 预编译正则用 \d 会匹配 Unicode 数字，这里一律用 [0-9] 更安全
_D = "[0-9]"


@dataclass
class ScrapeResult:
    file_path: str
    filename: str
    extracted_code: str
    status: str                 # "extracted" | "original"
    file_size: int
    inherited: bool = False     # ← 新增
    manually_edited: bool = False   # ← 新增：是否被用户手动修正过

    @property
    def is_extracted(self) -> bool:
        return self.status == "extracted"


class CodeExtractor:
    """根据给定配置构建一次性的提取器。配置改变时重建即可。"""

    def __init__(self, config: ScraperConfig):
        self.config = config
        self._video_exts = {e.lower() for e in config.video_extensions}
        self._attachment_exts = {e.lower() for e in config.attachment_extensions}
        self._supported_exts = self._video_exts | self._attachment_exts
        self._compile_patterns()

    # ---------- 预编译 ----------
    def _compile_patterns(self) -> None:
        seps = [re.escape(s) for s in self.config.separators]

        # 1. FC2
        # 连接符可有可无：实际文件里 FC2PPV1234567 / FC2-PPV-1234567 / FC2PPV-1234567 都常见。
        # 但"裸 FC2 + 数字"仍要求连接符，避免把 FC21234567 这类无关编号误认。
        # 两个模式都要 (?!\d)：否则 FC2-12345678 会被截成 FC2-1234567。
        self._fc2_patterns: list[re.Pattern] = []
        for s in seps:
            self._fc2_patterns.append(
                re.compile(rf"FC2(?:{s})?PPV(?:{s})?({_D}{{6,7}})(?!{_D})"))
            self._fc2_patterns.append(
                re.compile(rf"FC2{s}({_D}{{6,7}})(?!{_D})"))

        # 2. 字母前缀
        prefixes = sorted(
            {p.upper() for p in self.config.known_alpha_prefixes if p},
            key=len, reverse=True,
        )
        self._prefix_lookup: list[str] = []
        self._prefix_patterns: dict[str, list[re.Pattern]] = {}
        for prefix in prefixes:
            if prefix == "FC2":
                continue
            self._prefix_lookup.append(prefix)
            pats = [
                re.compile(
                    rf"({re.escape(prefix)}){s}({_D}{{2,4}})(?!{_D})")
                for s in seps
            ]
            self._prefix_patterns[prefix] = pats

        # 3. 六位数字前缀
        # (?<!\d) 不可省：否则 1234567-789 会从第 2 位开始匹配，抓出错误的 234567-789
        self._digital_patterns: list[re.Pattern] = [
            re.compile(rf"(?<!{_D})({_D}{{6}}){s}({_D}{{3}})(?!{_D})")
            for s in seps
        ]

    # ---------- 提取 ----------
    def extract(self, filename: str) -> str | None:
        """返回标准代码；未识别返回 None。"""
        upper = filename.upper()
        return (
            self._match_fc2(upper)
            or self._match_alpha(upper)
            or self._match_digital(upper)
        )

    def _match_fc2(self, upper: str) -> str | None:
        for pat in self._fc2_patterns:
            m = pat.search(upper)
            if m:
                return f"FC2-{m.group(1)}"
        return None

    def _match_alpha(self, upper: str) -> str | None:
        # 快速预筛：不含任何前缀就跳过
        if not any(p in upper for p in self._prefix_lookup):
            return None
        for pats in self._prefix_patterns.values():
            for pat in pats:
                m = pat.search(upper)
                if m:
                    return f"{m.group(1)}-{m.group(2)}"
        return None

    def _match_digital(self, upper: str) -> str | None:
        for pat in self._digital_patterns:
            m = pat.search(upper)
            if m:
                return f"{m.group(1)}-{m.group(2)}"
        return None

    # ---------- 扫描 ----------
    def is_supported(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in self._supported_exts

    def is_video(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in self._video_exts

    def is_attachment(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in self._attachment_exts

    def scan_directory(
        self,
        directory: str | Path,
        recursive: bool | None = None,
        progress: Callable[[ScrapeResult], None] | None = None,
        on_total: Callable[[int], None] | None = None,
    ) -> list[ScrapeResult]:
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(f"目录不存在：{directory}")

        if recursive is None:
            recursive = self.config.recursive_processing
        iterator = directory.rglob("*") if recursive else directory.glob("*")

        # 先一次性收集候选文件，得到总数
        candidates: list[Path] = []
        for path in iterator:
            try:
                if path.is_file() and self.is_supported(path.name):
                    candidates.append(path)
            except OSError:
                continue

        if on_total is not None:
            on_total(len(candidates))

        results: list[ScrapeResult] = []
        for path in candidates:
            try:
                code = self.extract(path.name)
                inherited = False

                # 文件名没提取到，尝试父目录名
                if code is None and self.config.inherit_from_parent:
                    parent_name = path.parent.name
                    if parent_name:
                        parent_code = self.extract(parent_name)
                        if parent_code is not None:
                            code = parent_code
                            inherited = True

                size = path.stat().st_size
            except OSError as e:
                logger.warning("读取失败 %s: %s", path.name, e)
                continue

            result = ScrapeResult(
                file_path=str(path),
                filename=path.name,
                extracted_code=code or path.name,
                status="extracted" if code else "original",
                file_size=size,
                inherited=inherited,
            )
            results.append(result)
            if progress is not None:
                progress(result)

        return results

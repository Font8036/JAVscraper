"""从文件名中提取标准化代码。

设计目标：宁可漏抓，不可误抓。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import ScraperConfig

logger = logging.getLogger(__name__)

# 预编译正则用 \d 会匹配 Unicode 数字，这里一律用 [0-9] 更安全
_D = "[0-9]"


@dataclass
class ScrapeResult:
    file_path: str
    filename: str
    extracted_code: str
    status: str  # "extracted" | "original"
    file_size: int

    @property
    def is_extracted(self) -> bool:
        return self.status == "extracted"


class CodeExtractor:
    """根据给定配置构建一次性的提取器。配置改变时重建即可。"""

    def __init__(self, config: ScraperConfig):
        self.config = config
        self._supported_exts = {e.lower() for e in config.supported_extensions}
        self._compile_patterns()

    # ---------- 预编译 ----------
    def _compile_patterns(self) -> None:
        seps = [re.escape(s) for s in self.config.separators]

        # 1. FC2
        self._fc2_patterns: list[re.Pattern] = []
        for s in seps:
            self._fc2_patterns.append(
                re.compile(rf"FC2{s}PPV{s}({_D}{{6,7}})"))
            self._fc2_patterns.append(
                re.compile(rf"FC2{s}({_D}{{6,7}})"))

        # 2. 字母前缀
        prefixes = sorted(set(self.config.known_alpha_prefixes),
                          key=len, reverse=True)
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
        self._digital_patterns: list[re.Pattern] = [
            re.compile(rf"({_D}{{6}}){s}({_D}{{3}})(?!{_D})")
            for s in seps
        ]

    # ---------- 提取 ----------
    def extract(self, filename: str) -> Optional[str]:
        """返回标准代码；未识别返回 None。"""
        upper = filename.upper()
        return (
            self._match_fc2(upper)
            or self._match_alpha(upper)
            or self._match_digital(upper)
        )

    def _match_fc2(self, upper: str) -> Optional[str]:
        for pat in self._fc2_patterns:
            m = pat.search(upper)
            if m:
                return f"FC2-{m.group(1)}"
        return None

    def _match_alpha(self, upper: str) -> Optional[str]:
        # 快速预筛：不含任何前缀就跳过
        if not any(p in upper for p in self._prefix_lookup):
            return None
        for prefix, pats in self._prefix_patterns.items():
            for pat in pats:
                m = pat.search(upper)
                if m:
                    return f"{m.group(1)}-{m.group(2)}"
        return None

    def _match_digital(self, upper: str) -> Optional[str]:
        for pat in self._digital_patterns:
            m = pat.search(upper)
            if m:
                return f"{m.group(1)}-{m.group(2)}"
        return None

    # ---------- 扫描 ----------
    def is_supported(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in self._supported_exts

    def scan_directory(
        self,
        directory: str | Path,
        recursive: Optional[bool] = None,
        progress: Optional[Callable[[ScrapeResult], None]] = None,
        on_total: Optional[Callable[[int], None]] = None,
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
            if not path.is_file() or not self.is_supported(path.name):
                continue
            try:
                code = self.extract(path.name)
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
            )
            results.append(result)
            if progress:
                progress(result)
        return results
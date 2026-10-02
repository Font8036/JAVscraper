"""按刮削结果移动 / 重命名 / 撤回文件。"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import ProcessorConfig

logger = logging.getLogger(__name__)


@dataclass
class PlannedOperation:
    src: Path
    dst: Path
    status: str  # "move" | "rename" | "skip" | "overwrite"
    extracted_code: str = ""   # ← 新增


@dataclass
class MoveOperation:
    original_path: str
    moved_to: str
    original_filename: str
    target_filename: str
    extracted_code: str


class FileProcessor:
    def __init__(self, config: ProcessorConfig):
        self.config = config

    # ---------- 载入结果 ----------
    @staticmethod
    def load_results(json_path: str | Path) -> list[dict]:
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 JSON 文件：{path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- 规划 ----------
    def plan(self, results: list[dict]) -> list[PlannedOperation]:
        extracted = [r for r in results if r.get("status") == "extracted"]
        taken: set[str] = set()
        planned: list[PlannedOperation] = []

        for r in extracted:
            src = Path(r["file_path"])
            code = r["extracted_code"]
            target = self._target_path(src, code)
            final, status = self._resolve_conflict(target, taken)
            planned.append(PlannedOperation(
                src=src,
                dst=final if final else target,
                status=status,
                extracted_code=code,
            ))
            taken.add(str(final if final else target))

        return planned

    def _target_path(self, src: Path, code: str) -> Path:
        name = f"{code}{src.suffix}" if self.config.enable_rename else src.name
        if self.config.move_to_extracted_folder:
            return Path(self.config.target_directory) / code / name
        return Path(self.config.target_directory) / name

    def _resolve_conflict(
        self, target: Path, taken: set[str],
    ) -> tuple[Optional[Path], str]:
        if not target.exists() and str(target) not in taken:
            return target, "move"

        handling = self.config.existing_file_handling
        if handling == "skip":
            return None, "skip"
        if handling == "overwrite":
            return target, "overwrite"

        # rename：追加 _01 / _02 …
        stem, suffix = target.stem, target.suffix
        i = 1
        while True:
            candidate = target.parent / f"{stem}_{i:02d}{suffix}"
            if not candidate.exists() and str(candidate) not in taken:
                return candidate, "rename"
            i += 1

    # ---------- 执行 ----------
    def execute(
        self,
        planned: list[PlannedOperation],
        progress: Optional[Callable[[int, int, PlannedOperation, str], None]] = None,
    ) -> tuple[list[MoveOperation], int, int, int]:
        """返回 (操作记录, 成功数, 跳过数, 失败数)。

        progress(idx, total, op, status)
          - idx:   当前第几个（从 1 开始）
          - total: 总数
          - op:    当前操作
          - status: "ok" / "skip" / "missing" / "error:<msg>"
        """
        ops: list[MoveOperation] = []
        success = skipped = failed = 0
        total = len(planned)

        for idx, p in enumerate(planned, start=1):
            if p.status == "skip":
                skipped += 1
                if progress:
                    progress(idx, total, p, "skip")
                continue

            if not p.src.exists():
                failed += 1
                if progress:
                    progress(idx, total, p, "missing")
                continue

            try:
                p.dst.parent.mkdir(parents=True, exist_ok=True)
                if p.status == "overwrite" and p.dst.exists():
                    p.dst.unlink()
                shutil.move(str(p.src), str(p.dst))
            except OSError as e:
                logger.warning("移动失败 %s: %s", p.src.name, e)
                failed += 1
                if progress:
                    progress(idx, total, p, f"error:{e}")
                continue

            code = p.dst.parent.name if self.config.move_to_extracted_folder else ""
            ops.append(MoveOperation(
                original_path=str(p.src),
                moved_to=str(p.dst),
                original_filename=p.src.name,
                target_filename=p.dst.name,
                extracted_code=code,
            ))
            success += 1
            if progress:
                progress(idx, total, p, "ok")

        return ops, success, skipped, failed

    # ---------- 记录 / 撤回 ----------
    @staticmethod
    def save_operations(ops: list[MoveOperation], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(o) for o in ops], f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_operations(path: Path) -> list[MoveOperation]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [MoveOperation(**d) for d in data]

    def undo(
        self,
        ops_file: Path,
        progress: Optional[Callable[[MoveOperation, str], None]] = None,
    ) -> tuple[int, int]:
        ops = self.load_operations(ops_file)
        success = failed = 0

        for op in ops:
            src = Path(op.moved_to)
            dst = Path(op.original_path)

            if not src.exists():
                failed += 1
                if progress:
                    progress(op, "missing")
                continue

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as e:
                logger.warning("撤回失败 %s: %s", src.name, e)
                failed += 1
                if progress:
                    progress(op, f"error:{e}")
                continue

            success += 1
            if progress:
                progress(op, "ok")

        # 只有全部成功才删记录文件；有失败的话保留，用户可重试
        if failed == 0 and success > 0:
            try:
                ops_file.unlink()
            except OSError:
                pass

        return success, failed
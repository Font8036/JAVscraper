"""按刮削结果移动 / 重命名 / 撤回文件。"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional
from .defaults import DEFAULT_ATTACHMENT_EXTENSIONS, DEFAULT_VIDEO_EXTENSIONS
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


def find_target_collisions(
    planned: Iterable[PlannedOperation],
) -> dict[str, list[PlannedOperation]]:
    """找出本批内会写到同一个目标路径的操作（"批内重名"）。

    冲突处理为「覆盖」时，先移入的文件会被后一个覆盖删除，所以预览阶段要提醒用户。
    skip 不写盘，不计入。返回 {目标路径: 涉及的操作}，只包含有冲突的分组。
    """
    groups: dict[str, list[PlannedOperation]] = {}
    for p in planned:
        if p.status == "skip":
            continue
        groups.setdefault(str(p.dst), []).append(p)
    return {dst: ops for dst, ops in groups.items() if len(ops) > 1}


class FileProcessor:
    def __init__(
        self,
        config: ProcessorConfig,
        *,
        video_exts: Optional[list[str]] = None,
        attachment_exts: Optional[list[str]] = None,
    ):
        self.config = config
        # 默认用常量兜底，方便单元测试；生产路径由调用方显式传入
        self._video_exts = {
            e.lower() for e in (video_exts or DEFAULT_VIDEO_EXTENSIONS)
        }
        self._attachment_exts = {
            e.lower() for e in (attachment_exts or DEFAULT_ATTACHMENT_EXTENSIONS)
        }

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
        if self.config.attachment_naming == "跟随视频命名":
            return self._plan_follow_video(extracted)
        return self._plan_by_code(extracted)

    def _plan_by_code(self, extracted: list[dict]) -> list[PlannedOperation]:
        taken: set[str] = set()
        planned: list[PlannedOperation] = []
        for r in extracted:
            src = Path(r["file_path"])
            code = r["extracted_code"]
            target = self._target_path(
                src, code,
                is_attachment=self._is_attachment(src),   # ← 新增
            )
            final, status = self._resolve_conflict(target, taken)
            planned.append(PlannedOperation(
                src=src,
                dst=final if final else target,
                status=status,
                extracted_code=code,
            ))
            taken.add(str(final if final else target))
        return planned

    def _plan_follow_video(self, extracted: list[dict]) -> list[PlannedOperation]:
        """同目录附件跟随视频命名。规则：

        - 先规划每个目录里的视频（按 `_target_path` 现有逻辑）。
        - 目录里恰好一个视频时，其他附件跟随该视频的目标名（换扩展名）。
        - 目录里 0 个或多个视频时，附件退回按码命名。

        只按目录分组，不做跨目录配对——跨目录需要用户二次扫描。
        """
        # 1. 按目录分组
        by_dir: dict[Path, list[dict]] = {}
        for r in extracted:
            d = Path(r["file_path"]).parent
            by_dir.setdefault(d, []).append(r)

        taken: set[str] = set()
        planned: list[PlannedOperation] = []

        def _emit(src: Path, target: Path, code: str) -> None:
            final, status = self._resolve_conflict(target, taken)
            planned.append(PlannedOperation(
                src=src,
                dst=final if final else target,
                status=status,
                extracted_code=code,
            ))
            taken.add(str(final if final else target))

        for items in by_dir.values():
            videos = [r for r in items if self._is_video(r["file_path"])]
            main_video = videos[0] if len(videos) == 1 else None

            # 主视频先规划，拿到它的目标路径
            video_target: Optional[Path] = None
            if main_video is not None:
                code = main_video["extracted_code"]
                target = self._target_path(Path(main_video["file_path"]), code)
                _emit(Path(main_video["file_path"]), target, code)
                video_target = target

            for r in items:
                if main_video is not None and r is main_video:
                    continue
                src = Path(r["file_path"])
                code = r["extracted_code"]
                if (main_video is not None
                        and video_target is not None
                        and self._is_attachment(r["file_path"])):
                    _emit(src, video_target.with_suffix(src.suffix), code)
                else:
                    _emit(src, self._target_path(src, code), code)

        return planned

    def _is_video(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in self._video_exts

    def _is_attachment(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in self._attachment_exts

    def _target_path(
        self, src: Path, code: str, *, is_attachment: bool = False,
    ) -> Path:
        if is_attachment:
            # follow_video 不会走到这里（plan() 已分派到 _plan_follow_video）
            mode = self.config.attachment_naming
            if mode == "保留原文件名":
                name = src.name
            else:                       # "code"
                name = f"{code}{src.suffix}"
        else:
            if self.config.video_naming == "保留原文件名":
                name = src.name
            else:                       # "code"
                name = f"{code}{src.suffix}"

        if self.config.move_to_extracted_folder:
            return Path(self.config.target_directory) / code / name
        return Path(self.config.target_directory) / name

    def _resolve_conflict(
        self, target: Path, taken: set[str],
    ) -> tuple[Optional[Path], str]:
        if not target.exists() and str(target) not in taken:
            return target, "move"

        handling = self.config.existing_file_handling
        if handling == "跳过":
            return None, "skip"
        if handling == "覆盖":
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

            if dst.exists():
                # 原路径又被占用（重新下载、手动放回等）：不覆盖任何东西，
                # 计入失败并保留记录，由用户自己决定怎么处理
                failed += 1
                if progress:
                    progress(op, "occupied")
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
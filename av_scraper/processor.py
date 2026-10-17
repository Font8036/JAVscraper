"""按刮削结果移动 / 重命名 / 撤回文件。"""

from __future__ import annotations
import os
import re
import json
import logging
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import ProcessorConfig
from .defaults import DEFAULT_ATTACHMENT_EXTENSIONS, DEFAULT_VIDEO_EXTENSIONS

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
        video_exts: list[str] | None = None,
        attachment_exts: list[str] | None = None,
    ):
        self.config = config
        # 默认用常量兜底，方便单元测试；生产路径由调用方显式传入
        self._video_exts = {
            e.lower() for e in (video_exts or DEFAULT_VIDEO_EXTENSIONS)
        }
        self._attachment_exts = {
            e.lower() for e in (attachment_exts or DEFAULT_ATTACHMENT_EXTENSIONS)
        }
        self._multipart_map: dict[str, str] = {}   # ← 新增：{绝对路径: 重命名后的文件名}

    # ---------- 载入结果 ----------
    @staticmethod
    def load_results(json_path: str | Path) -> list[dict]:
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 JSON 文件：{path}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # ---------- 规划 ----------

    def plan(self, results: list[dict]) -> list[PlannedOperation]:
        extracted = [r for r in results if r.get("status") == "extracted"]
        
        # 预处理：找出所有支持多分段重命名的视频
        self._multipart_map = {}
        if self.config.multipart_naming:
            by_code: dict[str, list[dict]] = {}
            for r in extracted:
                by_code.setdefault(r["extracted_code"], []).append(r)
            for code, items in by_code.items():
                self._multipart_map.update(self._extract_multipart(items, code))

        if self.config.attachment_naming == "跟随视频命名":
            return self._plan_follow_video(extracted)
        return self._plan_by_code(extracted)

    def _extract_multipart(self, items: list[dict], code: str) -> dict[str, str]:
        """尝试提取多分段视频的 -partN 重命名映射。
        
        成功返回 {绝对路径: 新文件名}，失败返回 {}（整组放弃）。
        """
        if not self.config.multipart_naming:
            return {}

        videos = [r for r in items if self._is_video(r["file_path"])]
        if len(videos) < 2:
            return {}

        stems = [Path(v["file_path"]).stem for v in videos]
        lcp = os.path.commonprefix(stems)
        lcs = os.path.commonprefix([s[::-1] for s in stems])[::-1]

        # 重叠保护：如果公共前缀+后缀把某个文件名切空了
        if len(lcp) + len(lcs) >= min(len(s) for s in stems):
            return {}

        indices = []
        for s in stems:
            end_idx = -len(lcs) if len(lcs) > 0 else None
            diff = s[len(lcp):end_idx]
            # 剔除两端非字母数字字符
            diff = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", diff)

            if diff.isdigit():
                indices.append(int(diff))
            elif len(diff) == 1 and diff.isalpha():
                indices.append(ord(diff.lower()) - ord('a') + 1)
            else:
                # 不支持纯字母+数字混杂或其他奇怪格式
                logger.info("多分段视频 %s：无法解析序号（差异部分为 '%s'），已放弃", code, diff)
                return {}

        # 必须无重复，且排序后严格等于 [1, 2, ..., N]
        if sorted(indices) != list(range(1, len(videos) + 1)):
            logger.info("多分段视频 %s：序号不连续或重复，已放弃 -partN 重命名", code)
            return {}

        # 确定主干
        if self.config.video_naming == "按提取码命名":
            base = code
        else:
            base = lcp.rstrip("-_ .")
            if not base:
                base = code

        result = {}
        for v, idx in zip(videos, indices):
            src = Path(v["file_path"])
            new_name = f"{base}-part{idx}{src.suffix}"
            result[str(src)] = new_name

        logger.info("检测到多分段视频 %s：已准备重命名为 %s-part1 等", code, base)
        return result

    def _plan_by_code(self, extracted: list[dict]) -> list[PlannedOperation]:
        taken: set[str] = set()
        planned: list[PlannedOperation] = []
        for r in extracted:
            src = Path(r["file_path"])
            code = r["extracted_code"]
            
            if str(src) in self._multipart_map:
                name = self._multipart_map[str(src)]
                if self.config.move_to_extracted_folder:
                    target = Path(self.config.target_directory) / code / name
                else:
                    target = Path(self.config.target_directory) / name
            else:
                target = self._target_path(
                    src, code, is_attachment=self._is_attachment(src),
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

        每个源目录独立处理：
        - 先看源目录里有没有视频。恰好一个 → 附件跟随它的目标名。
        - 源目录没有视频 → 到目标目录的对应番号文件夹里找。
          找到恰好一个视频 → 附件跟随它。
        - 如果开启了多分段且成功应用，取 part1 作为主视频
        - 两处都找不到、或找到多个 → 退回 attachment_naming 的其他规则。

        平铺模式（move_to_extracted_folder=False）不启用目标目录查找——
        没有明确的番号文件夹，无从查起。

        只按目录分组，不做跨目录配对。
        """
        by_dir: dict[Path, list[dict]] = {}
        for r in extracted:
            d = Path(r["file_path"]).parent
            by_dir.setdefault(d, []).append(r)

        taken: set[str] = set()
        planned: list[PlannedOperation] = []
        target_video_cache: dict[str, Optional[Path]] = {}

        def _emit(src: Path, target: Path, code: str) -> None:
            final, status = self._resolve_conflict(target, taken)
            planned.append(PlannedOperation(
                src=src, dst=final if final else target,
                status=status, extracted_code=code,
            ))
            taken.add(str(final if final else target))

        for items in by_dir.values():
            videos = [r for r in items if self._is_video(r["file_path"])]
            
            # 核心修改：如果开启了多分段且成功应用，取 part1 作为主视频
            main_video = None
            if len(videos) == 1:
                main_video = videos[0]
            elif len(videos) > 1:
                # 检查这组多视频是否全部命中了 _multipart_map
                if all(str(Path(v["file_path"])) in self._multipart_map for v in videos):
                    # 找到名字以 -part1 结尾的那个作为 main_video
                    for v in videos:
                        new_name = self._multipart_map[str(Path(v["file_path"]))]
                        if "-part1" in new_name:
                            main_video = v
                            break

            # 主视频先规划，拿到目标路径
            video_target: Optional[Path] = None
            if main_video is not None:
                code = main_video["extracted_code"]
                src = Path(main_video["file_path"])
                if str(src) in self._multipart_map:
                    name = self._multipart_map[str(src)]
                    if self.config.move_to_extracted_folder:
                        target = Path(self.config.target_directory) / code / name
                    else:
                        target = Path(self.config.target_directory) / name
                else:
                    target = self._target_path(src, code)
                _emit(src, target, code)
                video_target = target

            for r in items:
                if main_video is not None and r is main_video:
                    continue
                src = Path(r["file_path"])
                code = r["extracted_code"]
                is_att = self._is_attachment(src)
                
                # 如果当前文件也在 _multipart_map 里（即其它 part），按 part 规划
                if str(src) in self._multipart_map:
                    name = self._multipart_map[str(src)]
                    if self.config.move_to_extracted_folder:
                        target = Path(self.config.target_directory) / code / name
                    else:
                        target = Path(self.config.target_directory) / name
                    _emit(src, target, code)
                elif main_video is not None and video_target is not None and is_att:
                    _emit(src, video_target.with_suffix(src.suffix), code)
                elif is_att:
                    tv = self._find_target_video(code, cache=target_video_cache)
                    if tv is not None:
                        _emit(src, tv.parent / f"{tv.stem}{src.suffix}", code)
                    else:
                        _emit(src, self._target_path(src, code, is_attachment=True), code)
                else:
                    _emit(src, self._target_path(src, code, is_attachment=is_att), code)

        return planned

    def _is_video(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in self._video_exts

    def _is_attachment(self, path: str | Path) -> bool:
        return Path(path).suffix.lower() in self._attachment_exts

    def _find_target_video(
        self, code: str, *, cache: dict[str, Optional[Path]],
    ) -> Optional[Path]:
        """在目标目录的对应番号文件夹里找唯一视频。

        找不到、找到多个、平铺模式、目标目录不存在 —— 一律返回 None。
        调用方据此退回其他命名规则。
        """
        if code in cache:
            return cache[code]

        result: Optional[Path] = None
        if (self.config.move_to_extracted_folder
                and self.config.target_directory):
            folder = Path(self.config.target_directory) / code
            try:
                if folder.is_dir():
                    videos = [
                        p for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() in self._video_exts
                    ]
                    if len(videos) == 1:
                        result = videos[0]
            except OSError:
                pass

        cache[code] = result
        return result

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
    ) -> tuple[Path | None, str]:
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
        progress: Callable[[int, int, PlannedOperation, str], None] | None = None,
        on_moved: Callable[[MoveOperation], None] | None = None,
    ) -> tuple[list[MoveOperation], int, int, int]:
        """返回 (操作记录, 成功数, 跳过数, 失败数)。

        progress(idx, total, op, status)
          - idx:   当前第几个（从 1 开始）
          - total: 总数
          - op:    当前操作
          - status: "ok" / "skip" / "missing" / "error:<msg>"

        on_moved(op)：每成功移动一个文件就回调一次。给"边移动边落盘记录"用——
        命令行里中途 Ctrl+C 时，已经移动的文件仍然留在记录里，可以撤回。
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
            moved = MoveOperation(
                original_path=str(p.src),
                moved_to=str(p.dst),
                original_filename=p.src.name,
                target_filename=p.dst.name,
                extracted_code=code,
            )
            ops.append(moved)
            success += 1
            if on_moved is not None:
                on_moved(moved)
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
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [MoveOperation(**d) for d in data]

    def undo(
        self,
        ops_file: Path,
        progress: Callable[[MoveOperation, str], None] | None = None,
    ) -> tuple[int, int]:
        """按记录把文件搬回原位，返回 (成功数, 失败数)。

        可以重复执行：已经搬回去的文件第二次会被认成 "already" 并计入成功，
        所以上一次撤回被 Ctrl+C 打断后，直接再跑一次就行。
        """
        ops = self.load_operations(ops_file)
        success = failed = 0

        for op in ops:
            src = Path(op.moved_to)
            dst = Path(op.original_path)

            if not src.exists():
                if dst.exists():
                    # 已经撤回过了（上次撤回被打断、或用户自己搬了回来）：
                    # 目标位置就是它该在的地方，视为完成，不碰任何文件
                    success += 1
                    if progress:
                        progress(op, "already")
                else:
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

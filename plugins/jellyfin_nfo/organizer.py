"""遍历电影文件夹，复制封面，写 NFO。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

from .config import JellyfinNfoConfig
from .nfo_builder import build_nfo

logger = logging.getLogger(__name__)
LogFn = Callable[[str], None]

_VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".rmvb", ".mov", ".wmv",
    ".flv", ".webm", ".m4v", ".ts", ".m2ts",
}


def organize_movie_folder(
    folder_path: Path,
    code: str,
    movie_data: dict,
    config: JellyfinNfoConfig,
    on_log: Optional[LogFn] = None,
) -> dict:
    """处理一个电影文件夹。"""
    log = on_log or (lambda s: None)
    folder_path = Path(folder_path)

    result = {
        "code": code,
        "video_count": 0,
        "cover_ok": False,
        "nfo_ok": False,
        "message": "",
    }

    # 1. 找视频
    videos = _find_videos(folder_path)
    result["video_count"] = len(videos)
    if not videos:
        result["message"] = "无视频文件"
        log(f"  {code}: 未找到视频文件，跳过")
        return result

    # 2. 找封面 + 复制
    poster_filename = None
    if config.copy_cover and config.cover_source_dir:
        cover_src = _find_cover(config.cover_source_dir, code)
        if cover_src:
            copied = _copy_covers(folder_path, videos, code, cover_src, config)
            result["cover_ok"] = bool(copied)
            poster_filename = copied[0] if copied else None
        else:
            log(f"  {code}: 未找到封面源文件")

    # 3. 生成 NFO 内容
    nfo_content = build_nfo(movie_data, poster_filename)

    # 4. 按模式写 NFO
    written = _write_nfo_files(folder_path, videos, nfo_content, config)
    result["nfo_ok"] = written > 0
    result["message"] = f"NFO×{written}"
    return result


def _find_videos(folder: Path) -> list:
    return [
        p for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTS
    ]


def _find_cover(cover_dir, code: str) -> Optional[Path]:
    cover_dir = Path(cover_dir)
    if not cover_dir.exists():
        return None
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        candidate = cover_dir / f"{code}{ext}"
        if candidate.exists():
            return candidate
    return None


def _copy_covers(
    folder: Path, videos: list, code: str,
    cover_src: Path, config: JellyfinNfoConfig,
) -> list:
    copied: list = []
    ext = cover_src.suffix or ".jpg"

    if config.cover_naming in ("与视频同名", "两种都复制"):
        for v in videos:
            dst = folder / f"{v.stem}{ext}"
            try:
                shutil.copy2(cover_src, dst)
                copied.append(dst.name)
            except OSError as e:
                logger.warning("复制封面 %s 失败: %s", dst, e)

    if config.cover_naming in ("用番号命名", "两种都复制"):
        dst = folder / f"{code}{ext}"
        if dst.name not in copied:
            try:
                shutil.copy2(cover_src, dst)
                copied.append(dst.name)
            except OSError as e:
                logger.warning("复制封面 %s 失败: %s", dst, e)

    return copied


def _write_nfo_files(
    folder: Path, videos: list, content: str,
    config: JellyfinNfoConfig,
) -> int:
    written = 0

    if config.nfo_mode in ("每个视频生成同名 NFO", "两种都生成"):
        for v in videos:
            path = folder / f"{v.stem}.nfo"
            if _write_if_allowed(path, content, config.overwrite_existing):
                written += 1

    if config.nfo_mode in ("只生成 movie.nfo", "两种都生成"):
        path = folder / "movie.nfo"
        if _write_if_allowed(path, content, config.overwrite_existing):
            written += 1

    return written


def _write_if_allowed(path: Path, content: str, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        return False
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as e:
        logger.warning("写入 %s 失败: %s", path, e)
        return False
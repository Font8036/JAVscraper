"""Playwright 爬取 javdb。

对外暴露：
- load_targets(excel_path)        读番号清单
- scrape_javdb(config, targets)   异步爬取
- save_csv(records, path)         落盘
"""

from __future__ import annotations

import logging
import asyncio
import os
import threading
from pathlib import Path
from typing import Callable, Optional
from pathlib import Path
import httpx
import pandas as pd
from playwright.async_api import async_playwright

from .config import JavdbConfig

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, str, Optional[dict], str], None]
# (idx0, keyword, payload, status)
# payload = {"fanhao": ..., "name": ...} 或 None


# 这些字符串（不区分大小写）都视为空值
_INVALID_TOKENS = {"", "nan", "null", "none", "na", "n/a", "nat", "-"}

def _noop_log(msg: str) -> None:
    pass


# ============================================================
# 读番号清单
# ============================================================
def load_targets(excel_path: str | Path) -> list[str]:
    """读取 Excel 第一列作为番号列表。

    会跳过：
    - 真正的 NaN
    - 空字符串 / 纯空白
    - 字符串形式的 "nan" / "null" / "none" 等
    """
    df = pd.read_excel(excel_path, header=None, dtype=str)
    raw_count = len(df)

    targets: list[str] = []
    skipped = 0
    for v in df.iloc[:, 0]:
        if pd.isna(v):
            skipped += 1
            continue
        s = str(v).strip()
        if s.lower() in _INVALID_TOKENS:
            skipped += 1
            continue
        targets.append(s)

    logger.info(
        "读取 Excel：原始 %d 行，有效 %d 个，跳过 %d 行",
        raw_count, len(targets), skipped,
    )
    return targets


# ============================================================
# 爬取
# ============================================================
async def scrape_javdb(
    config: JavdbConfig,
    targets: list[str],
    *,
    stop_event: Optional[threading.Event] = None,
    on_log: LogFn = _noop_log,
    on_progress: Optional[ProgressFn] = None,
) -> list[dict]:
    table_data: list[dict] = []
    if not targets:
        on_log("没有待处理的番号")
        return table_data

    total = len(targets)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not config.show_browser,
            channel=config.browser_channel or None,
        )

        if config.state_file and os.path.exists(config.state_file):
            context = await browser.new_context(storage_state=config.state_file)
            on_log(f"已加载登录状态: {config.state_file}")
        else:
            context = await browser.new_context()
            on_log("未加载登录状态，以游客模式运行")

        page = await context.new_page()
        page.set_default_timeout(config.page_timeout)

        try:
            await page.goto("https://javdb.com/", wait_until="load")
            on_log(f"已打开网页: {page.url}")

            for idx, keyword in enumerate(targets):
                if stop_event and stop_event.is_set():
                    on_log("收到停止信号，中断爬取")
                    if on_progress:
                        on_progress(idx, keyword, None, "stopped")
                    break

                on_log(f"\n===== [{idx+1}/{total}] 正在处理: {keyword} =====")
                if on_progress:
                    on_progress(idx, keyword, None, "running")

                success = False
                last_error = ""
                for attempt in range(config.max_retries + 1):
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        record = await _process_one(page, keyword, config, on_log)
                        table_data.append(record)
                        on_log(f"抓取成功: {record['番号']} - {record['名称']}")
                        if on_progress:
                                on_progress(
                                idx, keyword,
                                {"fanhao": record["番号"],
                                 "name": record["名称"]},
                                "ok",
                            )
                        success = True
                        break
                    except Exception as e:
                        last_error = str(e)
                        if attempt < config.max_retries:
                            on_log(f"第 {attempt+1} 次失败，稍后重试: {e}")
                            await asyncio.sleep(config.request_delay * 2)
                        else:
                            on_log(f"处理 {keyword} 失败: {e}")

                if not success and not (stop_event and stop_event.is_set()):
                    if on_progress:
                        on_progress(idx, keyword, None, f"failed:{last_error}")

                # 回到搜索页
                try:
                    await page.go_back()
                    await page.wait_for_selector(
                        "#video-search", timeout=config.page_timeout)
                except Exception:
                    try:
                        await page.goto(
                            "https://javdb.com/", wait_until="load")
                    except Exception:
                        pass

        finally:
            # 保存登录状态
            if config.save_state_on_exit and config.state_file:
                try:
                    await context.storage_state(path=config.state_file)
                    on_log(f"登录状态已保存: {config.state_file}")
                except Exception as e:
                    on_log(f"保存登录状态失败: {e}")
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    return table_data


async def _process_one(
    page,
    keyword: str,
    config: JavdbConfig,
    on_log: LogFn,
) -> dict:
    # ---- 搜索 ----
    await page.fill("#video-search", "")
    await page.fill("#video-search", keyword)
    await page.wait_for_selector("#search-submit", timeout=config.page_timeout)
    await page.click("#search-submit")
    await page.wait_for_selector(
        ".movie-list .item a", state="visible", timeout=config.page_timeout,
    )

    cover_url = await page.get_attribute(".movie-list .item img", "src")

    # ---- 进入详情 ----
    await page.click(".movie-list .item a")
    await page.wait_for_selector(
        "strong.current-title", state="visible", timeout=config.page_timeout,
    )

    try:
        fanhao = await page.inner_text(
            "html > body > section > div > div:nth-of-type(4) "
            "> div > div > div:nth-of-type(2) > nav > div > span"
        )
    except Exception:
        fanhao = keyword

    # ---- 可选点击 a.meta-link ----
    try:
        meta_link = await page.query_selector("a.meta-link")
        if meta_link:
            await page.click("a.meta-link", timeout=5000)
            on_log("已点击 a.meta-link")
            await page.wait_for_selector(
                "strong.current-title", state="visible",
                timeout=config.page_timeout,
            )
    except Exception as e:
        on_log(f"处理 a.meta-link 时出错（已忽略继续）: {e}")

    try:
        name = await page.inner_text("strong.current-title")
    except Exception:
        name = ""

    try:
        info = await page.inner_text(
            "html > body > section > div > div:nth-of-type(4) "
            "> div > div > div:nth-of-type(2)"
        )
    except Exception:
        info = ""

    # ---- 评论 ----
    await asyncio.sleep(config.request_delay)
    try:
        await page.wait_for_selector(".review-tab", timeout=10000)
        await page.click(".review-tab")
        await page.wait_for_selector("#reviews", timeout=10000)
        comments = await page.inner_text("#reviews > article > div")
    except Exception:
        comments = "暫無內容"

    await asyncio.sleep(config.request_delay)

    # ---- 下载封面 ----
    if cover_url and config.cover_dir:
        img_path = Path(config.cover_dir) / f"{fanhao}.jpg"
        if config.skip_existing_covers and img_path.exists():
            on_log(f"封面已存在，跳过: {img_path}")
        else:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(cover_url)
                    if resp.is_success:
                        img_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(img_path, "wb") as f:
                            f.write(resp.content)
                        on_log(f"封面已下载: {img_path}")
                    else:
                        on_log(f"封面下载失败: HTTP {resp.status_code}")
            except Exception as e:
                on_log(f"封面下载异常: {e}")

    return {
        "目标番号": keyword,          # ← 新增
        "链接": page.url,
        "番号": fanhao,
        "名称": name,
        "信息": info,
        "评论": comments,
    }


# ============================================================
# 落盘
# ============================================================
def save_csv(records: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(path, index=False, encoding="utf-8-sig")
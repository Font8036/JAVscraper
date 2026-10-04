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
# 新增
import csv
from openpyxl import load_workbook
from playwright.async_api import async_playwright

from .config import JavdbConfig

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, str, Optional[dict], str], None]
# (idx0, keyword, payload, status)
# payload = {"fanhao": ..., "name": ...} 或 None

class NoSearchResultError(Exception):
    """javdb 搜索返回空结果。调用方应直接跳过，不重试。"""

    def __init__(self, keyword: str):
        super().__init__(f"搜索无结果: {keyword}")
        self.keyword = keyword


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
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel 不存在：{path}")

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        if ws is None:
            return []
        targets: list[str] = []
        for row in ws.iter_rows(min_col=1, max_col=1, values_only=True):
            v = row[0] if row else None
            if v is None:
                continue
            s = str(v).strip()
            if s and s.lower() not in _INVALID_TOKENS:
                targets.append(s)
        return targets
    finally:
        wb.close()


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
        page.set_default_timeout(config.page_timeout_ms)

        try:
            # 首次打开主页，网络慢，给 5 倍超时
            on_log(f"正在打开主页（超时 {config.page_timeout_ms * 5 // 1000} 秒）…")
            await page.goto(config.base_url, wait_until="load", timeout=config.page_timeout_ms * 5)
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

                    except NoSearchResultError:
                        # 无搜索结果：直接记为失败，不重试
                        last_error = "无搜索结果"
                        on_log(f"{keyword}: 搜索无结果，跳过")
                        break

                    except Exception as e:
                        last_error = str(e)
                        if attempt < config.max_retries:
                            on_log(f"第 {attempt+1} 次失败，稍后重试: {e}")
                            await asyncio.sleep(config.request_delay * 2)
                        else:
                            on_log(f"处理 {keyword} 失败: {e}")

                if not success and not (stop_event and stop_event.is_set()):
                    # 失败也记一条，保证最终报告里能看到这个番号
                    table_data.append({
                        "目标番号": keyword,
                        "链接": "",
                        "番号": "",
                        "名称": "",
                        "信息": "",
                        "评论": "",
                    })
                    if on_progress:
                        on_progress(idx, keyword, None, f"failed:{last_error}")

                # 回到搜索页
                try:
                    await page.go_back()
                    await page.wait_for_selector(
                        "#video-search", timeout=config.page_timeout_ms)
                except Exception:
                    try:
                        await page.goto(
                            config.base_url, wait_until="load")
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
    await page.wait_for_selector("#search-submit", timeout=config.page_timeout_ms)
    await page.click("#search-submit")
    # 结果出现、或"无结果"提示出现，谁先到就响应
    await page.wait_for_selector(
        ".movie-list .item a, .empty-message", state="visible", timeout=config.page_timeout_ms,
    )

    # 如果命中的是"无结果"提示，直接抛异常，避免无意义的重试
    empty = await page.query_selector(".empty-message")
    if empty and await empty.is_visible():
        raise NoSearchResultError(keyword)

    cover_url = await page.get_attribute(".movie-list .item img", "src")

    # ---- 进入详情 ----
    await page.click(".movie-list .item a")
    await page.wait_for_selector(
        "strong.current-title", state="visible", timeout=config.page_timeout_ms,
    )

    try:
        fanhao = await page.inner_text(
            "html > body > section > div > div:nth-of-type(4) "
            "> div > div > div:nth-of-type(2) > nav > div > span"
        )
    except Exception:
        fanhao = keyword

    # ---- 可选点击 a.meta-link ----
    await asyncio.sleep(config.request_delay)
    try:
        meta_link = await page.query_selector("a.meta-link")
        if meta_link:
            await page.click("a.meta-link", timeout=5000)
            on_log("已点击 a.meta-link")
            await page.wait_for_selector(
                "strong.current-title", state="visible",
                timeout=config.page_timeout_ms,
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

    if not records:
        # 空列表也要写一个文件，否则下游读会报"文件不存在"
        path.write_text("", encoding="utf-8-sig")
        return

    # 用第一条记录的键作为表头；后续记录缺的字段填空
    fieldnames = list(records[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: (r.get(k) or "") for k in fieldnames})

# ============================================================
# 独立浏览器会话：用户手动登录后保存登录状态
# ============================================================
class BrowserSession:
    """一个独立的 Playwright 浏览器会话，供用户手动登录使用。

    生命周期：
      start()  → 后台线程启动浏览器并打开站点首页
      用户操作 → 在浏览器里慢慢登录
      stop()   → 通知后台线程保存 storage_state 并关闭浏览器

    检测浏览器是否存活：用户直接关闭浏览器窗口（点 ×）时，
    Playwright 会触发 disconnected，这时无法再保存状态，
    会记录一条警告。
    """

    def __init__(self, config, on_log: Optional[LogFn] = None):
        self.config = config
        self._on_log = on_log or (lambda _s: None)
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._done_event = threading.Event()
        self._error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None

    # ---------- 状态查询 ----------
    @property
    def ready(self) -> bool:
        return self._ready_event.is_set()

    @property
    def done(self) -> bool:
        return self._done_event.is_set()

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    # ---------- 对外控制 ----------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """请求保存并关闭。"""
        self._stop_event.set()

    # ---------- 后台线程 ----------
    def _run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as e:
            self._error = e
            self._on_log(f"[错误] 浏览器会话异常：{e}")
            self._ready_event.set()  # 让 GUI 不再无限等待
        finally:
            self._done_event.set()

    async def _async_main(self) -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,     # 必须显示窗口让用户登录
                channel=self.config.browser_channel or None,
            )

            # 已有登录状态则加载
            state_file = (self.config.state_file or "").strip()
            context = None
            if state_file and Path(state_file).exists():
                try:
                    context = await browser.new_context(storage_state=state_file)
                    self._on_log(f"已加载登录状态：{state_file}")
                except Exception as e:
                    self._on_log(f"[警告] 加载登录状态失败，使用空会话：{e}")
            if context is None:
                context = await browser.new_context()

            page = await context.new_page()
            await page.goto(self.config.base_url, wait_until="load")

            self._ready_event.set()
            self._on_log("浏览器已启动，请在浏览器窗口中完成登录。")
            self._on_log("登录完成后，点击「关闭浏览器」按钮保存登录状态。")
            self._on_log("（直接点浏览器右上角的 × 关闭不会保存登录状态）")

            # 事件监听：浏览器整体断开时立即标记
            disconnected = asyncio.Event()

            def _on_disconnect(_browser=None):
                disconnected.set()

            browser.on("disconnected", _on_disconnect)

            # 等待用户通过按钮请求关闭
            try:
                while not self._stop_event.is_set():
                    # 1. 浏览器整体断开
                    if disconnected.is_set() or not browser.is_connected():
                        self._on_log(
                            "[警告] 浏览器被手动关闭，登录状态未保存。"
                        )
                        return

                    # 2. 所有页面都已被关闭（用户点 × 的典型情况）
                    alive_pages = [
                        p for p in context.pages if not p.is_closed()
                    ]
                    if not alive_pages:
                        self._on_log(
                            "[警告] 检测到所有页面已关闭，登录状态未保存。"
                        )
                        return

                    await asyncio.sleep(0.3)

                # 保存登录状态
                if state_file:
                    try:
                        target = Path(state_file)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        await context.storage_state(path=str(target))
                        self._on_log(f"登录状态已保存：{target}")
                    except Exception as e:
                        self._on_log(f"[警告] 保存登录状态失败：{e}")
                else:
                    self._on_log(
                        "[提示] 未配置「登录状态」路径，登录状态未保存。"
                    )
            finally:
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
                self._on_log("浏览器已关闭。")
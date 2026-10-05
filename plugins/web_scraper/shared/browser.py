"""共享的 Playwright 会话：供用户手动登录后保存状态。"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import async_playwright

LogFn = Callable[[str], None]


class BrowserSession:
    """独立的 Playwright 浏览器会话，供用户手动登录使用。

    用法：
        session = BrowserSession(base_url=..., channel=..., state_file=..., on_log=...)
        session.start()
        # 用户操作…
        session.stop()          # 请求保存并关闭
        session.done            # 检查是否已结束
    """

    def __init__(
        self,
        *,
        base_url: str,
        channel: str,
        state_file: str,
        on_log: Optional[LogFn] = None,
    ):
        self.base_url = base_url
        self.channel = channel
        self.state_file = state_file
        self._on_log = on_log or (lambda _s: None)

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._done_event = threading.Event()
        self._error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def ready(self) -> bool:
        return self._ready_event.is_set()

    @property
    def done(self) -> bool:
        return self._done_event.is_set()

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as e:
            self._error = e
            self._on_log(f"[错误] 浏览器会话异常：{e}")
            self._ready_event.set()
        finally:
            self._done_event.set()

    async def _async_main(self) -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                channel=self.channel or None,
            )

            context = None
            state_file = (self.state_file or "").strip()
            if state_file and Path(state_file).exists():
                try:
                    context = await browser.new_context(storage_state=state_file)
                    self._on_log(f"已加载登录状态：{state_file}")
                except Exception as e:
                    self._on_log(f"[警告] 加载登录状态失败，使用空会话：{e}")
            if context is None:
                context = await browser.new_context()

            page = await context.new_page()
            await page.goto(self.base_url, wait_until="load")

            self._ready_event.set()
            self._on_log("浏览器已启动，请在浏览器窗口中完成登录。")
            self._on_log("登录完成后，点击「关闭浏览器」按钮保存登录状态。")
            self._on_log("（直接点浏览器右上角的 × 关闭不会保存登录状态）")

            disconnected = asyncio.Event()

            def _on_disconnect(_browser=None):
                disconnected.set()

            browser.on("disconnected", _on_disconnect)

            try:
                while not self._stop_event.is_set():
                    if disconnected.is_set() or not browser.is_connected():
                        self._on_log(
                            "[警告] 浏览器被手动关闭，登录状态未保存。"
                        )
                        return

                    alive = [p for p in context.pages if not p.is_closed()]
                    if not alive:
                        self._on_log(
                            "[警告] 检测到所有页面已关闭，登录状态未保存。"
                        )
                        return

                    await asyncio.sleep(0.3)

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
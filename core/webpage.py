"""直接截取 DAK.GG 官网个人页，作为个人页面的首选展示方式。"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from .api import DakGGError


class WebCaptureError(DakGGError):
    """网页截图不可用或截取失败。"""


_TAB_PATHS = {
    "overview": "",
    "character": "/character",
    "matches": "/matches",
    "skins": "/skins",
}

_MODE_QUERY = {
    0: "ALL",
    2: "NORMAL",
    3: "RANK",
    6: "COBALT",
}


def find_browser() -> str | None:
    """查找本机可用的 Chromium 内核浏览器。"""
    env = os.environ.get("ER_BROWSER_PATH")
    if env and Path(env).is_file():
        return env
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for path in candidates:
        if Path(path).is_file():
            return path
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


class DakGGWebCapture:
    """用 Playwright 驱动系统 Edge/Chrome，截取官网内容容器。"""

    def __init__(self, output_dir: str | None = None, timeout: int = 60) -> None:
        self.output_dir = Path(
            output_dir or (Path(tempfile.gettempdir()) / "astrbot_er_helper_web")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = max(15, int(timeout or 60))
        self._playwright = None
        self._browser = None
        self._closed = False
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._closed:
            raise WebCaptureError("网页截图器已关闭。")
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return self._browser
            except Exception:  # noqa: BLE001
                self._browser = None
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise WebCaptureError(
                "未安装 playwright，无法使用网页截图；请安装后重试，或关闭 webpage_screenshot。"
            ) from exc
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        executable = find_browser()
        try:
            if executable:
                self._browser = await self._playwright.chromium.launch(
                    executable_path=executable,
                    headless=True,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
                )
            else:
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
                )
        except Exception as exc:  # noqa: BLE001
            raise WebCaptureError(
                "未找到可用的 Edge/Chrome 浏览器；请安装浏览器，或设置 ER_BROWSER_PATH。"
            ) from exc
        return self._browser

    async def capture(
        self,
        nickname: str,
        *,
        tab: str = "overview",
        matching_mode: int = 3,
    ) -> str:
        if tab not in _TAB_PATHS:
            raise WebCaptureError(f"不支持的官网页面：{tab}")
        async with self._lock:
            browser = await self._ensure_browser()
            context = await browser.new_context(
                viewport={"width": 1280, "height": 1600},
                device_scale_factor=1,
                proxy={"server": self.proxy} if self.proxy else None,
            )
            page = await context.new_page()
            try:
                url = (
                    f"https://dak.gg/er/players/{quote(nickname, safe='')}"
                    f"{_TAB_PATHS[tab]}?gameMode={_MODE_QUERY.get(matching_mode, 'RANK')}"
                )
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout * 1000
                )
                if response is not None and response.status >= 400:
                    raise WebCaptureError(f"官网返回 HTTP {response.status}。")
                await page.wait_for_selector("#content-container", timeout=self.timeout * 1000)
                try:
                    await page.wait_for_function(
                        "() => !document.querySelector('#content-container .animate-pulse')",
                        timeout=self.timeout * 1000,
                    )
                except Exception:  # noqa: BLE001 - 某些页面可能没有骨架屏
                    pass
                try:
                    await page.wait_for_function(
                        """(tab) => {
                          const text = document.querySelector('#content-container')?.innerText || '';
                          if (text.length < 300) return false;
                          if (tab === 'overview') return text.includes('游戏场次') || text.includes('平均TK');
                          if (tab === 'character') return text.includes('按武器');
                          if (tab === 'matches') return text.includes('最近20场') || text.includes('查看更多');
                          return text.includes('按皮肤统计');
                        }""",
                        arg=tab,
                        timeout=self.timeout * 1000,
                    )
                except Exception:  # noqa: BLE001 - 空数据页面也允许截图
                    pass
                await page.wait_for_timeout(1200)
                container = page.locator("#content-container")
                if await container.count() == 0:
                    raise WebCaptureError("官网个人页内容容器不存在。")
                page_text = await container.inner_text()
                if len(page_text) < 300 or "找不到" in page_text or "不存在" in page_text:
                    raise WebCaptureError("官网没有返回有效的玩家页面。")
                path = self.output_dir / (
                    f"web-{tab}-{int(asyncio.get_running_loop().time())}-{uuid.uuid4().hex[:8]}.png"
                )
                await container.screenshot(path=str(path), animations="disabled")
                if not path.is_file() or path.stat().st_size < 20_000:
                    raise WebCaptureError("官网截图内容为空。")
                self._prune()
                return str(path)
            except WebCaptureError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise WebCaptureError(f"截取官网页面失败：{exc}") from exc
            finally:
                await context.close()

    def _prune(self) -> None:
        files = sorted(
            self.output_dir.glob("web-*.png"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in files[40:]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass

    async def close(self) -> None:
        self._closed = True
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

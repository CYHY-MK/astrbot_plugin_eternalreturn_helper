"""DAK.GG 美术资源下载与缓存（角色立绘、武器/装备图标、段位图标）。"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import time
from pathlib import Path

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CACHE_TTL = 7 * 24 * 60 * 60


class AssetCache:
    """按 URL 缓存图片到系统临时目录，避免重复下载。"""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        proxy: str = "",
        cache_dir: str | None = None,
    ) -> None:
        self.timeout = max(5.0, float(timeout))
        self.proxy = (proxy or "").strip() or None
        self.cache_dir = Path(cache_dir or (Path(tempfile.gettempdir()) / "astrbot_er_lomu_assets"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session: aiohttp.ClientSession | None = None
        self._mem: dict[str, bytes] = {}
        self._semaphore = asyncio.Semaphore(8)

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={"User-Agent": USER_AGENT},
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _path_for(self, url: str) -> Path:
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
            suffix = ".img"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}{suffix}"

    async def get(self, url: str | None) -> bytes | None:
        if not url:
            return None
        url = url.strip()
        if not url:
            return None
        if url.startswith("//"):
            url = "https:" + url
        if url in self._mem:
            return self._mem[url]

        path = self._path_for(url)
        if path.exists():
            try:
                if time.time() - path.stat().st_mtime < CACHE_TTL:
                    data = path.read_bytes()
                    self._mem[url] = data
                    return data
            except OSError:
                pass

        async with self._semaphore:
            if self._session is None or self._session.closed:
                await self.start()
            assert self._session is not None
            try:
                async with self._session.get(url, proxy=self.proxy) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

        if not data:
            return None
        try:
            path.write_bytes(data)
        except OSError:
            pass
        self._mem[url] = data
        return data

    async def get_many(self, urls: list[str | None]) -> dict[str, bytes]:
        unique = []
        seen = set()
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(url)
        results = await asyncio.gather(*(self.get(url) for url in unique))
        return {url: data for url, data in zip(unique, results, strict=False) if data}

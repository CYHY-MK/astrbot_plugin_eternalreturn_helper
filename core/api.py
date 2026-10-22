"""DAK.GG 数据接口封装。

所有请求均为异步（aiohttp），并带有进程内 TTL 缓存与并发去重，
避免同一条指令短时间重复请求上游。数据来源：https://dak.gg/er
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

STATIC_TTL = 6 * 60 * 60  # 角色、装备等静态数据缓存 6 小时

_SCRIPT_RE = re.compile(r"(?is)<script\b([^>]*)>(.*?)</script\s*>")
_NEXT_DATA_ID_RE = re.compile(
    r"""(?i)\bid\s*=\s*(?:"__NEXT_DATA__"|'__NEXT_DATA__'|__NEXT_DATA__)"""
)


class DakGGError(Exception):
    """DAK.GG 接口异常基类。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class PlayerNotFound(DakGGError):
    """玩家不存在。"""


class RateLimited(DakGGError):
    """请求被限流。"""


class UpstreamError(DakGGError):
    """上游返回异常数据。"""


class ClientClosed(DakGGError):
    """客户端已关闭，拒绝重新创建连接。"""


class DakGGClient:
    """轻量的 DAK.GG 客户端。"""

    def __init__(
        self,
        *,
        api_base: str = "https://er.dakgg.io/api",
        timeout: float = 20.0,
        proxy: str = "",
        cache_ttl: int = 300,
    ) -> None:
        self.api_base = (api_base or "https://er.dakgg.io/api").rstrip("/")
        self.timeout = max(5.0, float(timeout or 20))
        self.proxy = (proxy or "").strip() or None
        self.cache_ttl = max(0, int(cache_ttl or 0))
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._closed = False
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )

    async def close(self) -> None:
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._cache.clear()
        self._locks.clear()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise ClientClosed("客户端已关闭，请重新加载插件。")
        if self._session is None or self._session.closed:
            await self.start()
        assert self._session is not None
        return self._session

    # ------------------------------------------------------------------ 缓存
    def _cached(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._cache.pop(key, None)
            return None
        return value

    def _store(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        # 简单防止缓存无限增长
        if len(self._cache) > 512:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (time.monotonic() + ttl, value)

    def invalidate(self, prefix: str = "") -> None:
        if not prefix:
            self._cache.clear()
            return
        for key in [k for k in self._cache if prefix in k]:
            self._cache.pop(key, None)

    # ------------------------------------------------------------------ HTTP
    async def _fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: float = 0,
        expect_json: bool = True,
        player_scope: bool = False,
    ) -> Any:
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
        if ttl > 0:
            cached = self._cached(url)
            if cached is not None:
                return cached

        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            if ttl > 0:
                cached = self._cached(url)
                if cached is not None:
                    return cached

            session = await self._get_session()
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    async with session.get(url, proxy=self.proxy) as resp:
                        text = await resp.text()
                        if resp.status == 404:
                            if player_scope:
                                raise PlayerNotFound(
                                    "没有找到该玩家，请检查昵称是否完整。", status=404
                                )
                            raise UpstreamError("DAK.GG 没有找到相关数据。", status=404)
                        if resp.status == 429:
                            wait = _retry_after(resp.headers.get("Retry-After"))
                            last_error = RateLimited(
                                f"DAK.GG 请求过于频繁，请 {wait:.0f} 秒后再试。", status=429
                            )
                            await asyncio.sleep(wait)
                            continue
                        if resp.status >= 500:
                            last_error = UpstreamError(
                                f"DAK.GG 服务异常（HTTP {resp.status}），请稍后再试。",
                                status=resp.status,
                            )
                            await asyncio.sleep(0.8 * (attempt + 1))
                            continue
                        if resp.status >= 400:
                            detail = _error_message(text)
                            suffix = f"：{detail}" if detail else ""
                            raise UpstreamError(
                                f"DAK.GG 请求失败（HTTP {resp.status}{suffix}）。",
                                status=resp.status,
                            )
                        if not expect_json:
                            self._store(url, text, ttl)
                            return text
                        try:
                            data = json.loads(text)
                        except ValueError as exc:
                            # 部分接口在玩家不存在时返回 200 + error 对象
                            _raise_for_error_payload(text, player_scope=player_scope)
                            raise UpstreamError("DAK.GG 返回了无法解析的数据。") from exc
                        _raise_for_error_payload(data, player_scope=player_scope)
                        self._store(url, data, ttl)
                        return data
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = exc
                    await asyncio.sleep(0.6 * (attempt + 1))
            if isinstance(last_error, DakGGError):
                raise last_error
            raise UpstreamError("连接 DAK.GG 失败，请检查网络或稍后再试。") from last_error

    async def get_api(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: float = 0,
        player_scope: bool = False,
    ) -> Any:
        url = f"{self.api_base}/{path.lstrip('/')}"
        return await self._fetch(url, params=params, ttl=ttl, player_scope=player_scope)

    async def get_page(self, url: str, *, ttl: float = 0) -> str:
        return await self._fetch(url, ttl=ttl, expect_json=False)

    # ------------------------------------------------------------------ 基础数据
    async def current_season(self) -> dict:
        return await self.get_api("/v0/current-season", params={"hl": "zh_CN"}, ttl=STATIC_TTL)

    async def seasons(self) -> dict:
        return await self.get_api("/v1/data/seasons", params={"hl": "zh_CN"}, ttl=STATIC_TTL)

    async def characters(self) -> dict:
        return await self.get_api("/v1/data/characters", params={"hl": "zh_CN"}, ttl=STATIC_TTL)

    async def masteries(self) -> dict:
        return await self.get_api("/v1/data/masteries", params={"hl": "zh_cn"}, ttl=STATIC_TTL)

    async def tiers(self) -> dict:
        return await self.get_api("/v1/data/tiers", params={"hl": "zh_cn"}, ttl=STATIC_TTL)

    async def items(self) -> dict:
        return await self.get_api("/v1/data/items", params={"hl": "zh-cn"}, ttl=STATIC_TTL)

    async def skills(self) -> dict:
        return await self.get_api("/v1/data/skills", params={"hl": "zh_cn"}, ttl=STATIC_TTL)

    async def trait_skills(self) -> dict:
        return await self.get_api("/v1/data/trait-skills", params={"hl": "zh_cn"}, ttl=STATIC_TTL)

    # ------------------------------------------------------------------ 玩家
    async def profile(self, nickname: str, *, ttl: float | None = None) -> dict:
        ttl = self.cache_ttl if ttl is None else ttl
        return await self.get_api(
            f"/v1/players/{quote(nickname, safe='')}/profile",
            params={"hl": "zh_CN"},
            ttl=ttl,
            player_scope=True,
        )

    async def matches(
        self,
        nickname: str,
        *,
        season_key: str | None = None,
        matching_mode: str = "RANK",
        team_mode: str = "ALL",
        page: int = 1,
        ttl: float | None = None,
    ) -> dict:
        ttl = self.cache_ttl if ttl is None else ttl
        params = {
            "matchingMode": matching_mode,
            "teamMode": team_mode,
            "page": str(page),
        }
        if season_key:
            params["season"] = season_key
        return await self.get_api(
            f"/v1/players/{quote(nickname, safe='')}/matches",
            params=params,
            ttl=ttl,
            player_scope=True,
        )

    async def sync_player(self, nickname: str) -> dict:
        """请求 DAK.GG 与官方服务器同步玩家数据（尽力而为）。"""
        return await self.get_api(
            f"/v0/rpc/player-sync/by-name/{quote(nickname, safe='')}",
            ttl=0,
            player_scope=True,
        )

    # ------------------------------------------------------------------ 统计
    async def character_stats(
        self,
        *,
        team_mode: str = "SQUAD",
        matching_mode: str = "RANK",
        tier: str = "diamond_plus",
        days: int = 7,
    ) -> dict:
        return await self.get_api(
            "/v1/character-stats",
            params={
                "dt": str(days),
                "matchingMode": matching_mode,
                "teamMode": team_mode,
                "tier": tier,
            },
            ttl=1800,
        )

    async def tier_distribution(self, *, team_mode: str = "SQUAD") -> dict:
        return await self.get_api(
            "/v0/statistics/tier-distribution",
            params={"teamMode": team_mode, "hl": "zh_CN"},
            ttl=1800,
        )

    async def leaderboard(
        self,
        *,
        season_key: str,
        server_name: str = "seoul",
        team_mode: str = "SQUAD",
        page: int = 1,
    ) -> dict:
        return await self.get_api(
            "/v0/leaderboard",
            params={
                "page": str(page),
                "seasonKey": season_key,
                "serverName": server_name,
                "teamMode": team_mode,
                "hl": "zh_CN",
            },
            ttl=1800,
        )

    # ------------------------------------------------------------------ 角色详情
    async def character_detail(
        self,
        character_key: str,
        *,
        team_mode: str = "SQUAD",
        matching_mode: str = "RANK",
        tier: str | None = "diamond_plus",
        ttl: float = 1800,
    ) -> dict:
        """抓取 dak.gg 角色详情页并解析内嵌的 __NEXT_DATA__。"""
        params = {
            "hl": "zh_CN",
            "tab": "introduction",
            "teamMode": team_mode,
            "matchingMode": matching_mode,
        }
        if tier:
            params["tier"] = tier
        url = f"https://dak.gg/er/characters/{quote(character_key, safe='')}?{urlencode(params)}"
        html = await self.get_page(url, ttl=ttl)
        payload = extract_next_data(html)
        if not payload:
            raise UpstreamError("角色详情页解析失败，请稍后再试。")
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise UpstreamError("角色详情页数据解析失败，请稍后再试。") from exc

        page_props = (data.get("props") or {}).get("pageProps") or {}
        snapshot = None
        for query in (page_props.get("dehydratedState") or {}).get("queries") or []:
            state = query.get("state") or {}
            candidate = state.get("data") or {}
            if isinstance(candidate, dict) and candidate.get("characterDetailStatSnapshot"):
                snapshot = candidate
                break
        if not snapshot:
            raise UpstreamError("角色详情页暂未提供该角色的统计数据。")
        snapshot = dict(snapshot)
        snapshot["character"] = page_props.get("character") or {}
        snapshot["teamMode"] = page_props.get("teamMode")
        snapshot["matchingMode"] = page_props.get("matchingMode")
        return snapshot


def extract_next_data(html: str) -> str | None:
    """从 Next.js 页面中提取 __NEXT_DATA__ JSON 字符串。"""
    scripts = _SCRIPT_RE.findall(html)
    for attrs, body in scripts:
        if _NEXT_DATA_ID_RE.search(attrs):
            return body
    # 兼容页面结构变动：直接找包含目标快照的脚本
    for _attrs, body in scripts:
        if "dehydratedState" in body and "characterDetailStatSnapshot" in body:
            return body
    return None


def _error_message(payload: str) -> str:
    try:
        data = json.loads(payload)
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message[:80]
    return ""


def _raise_for_error_payload(payload: Any, *, player_scope: bool = False) -> None:
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if not isinstance(error, dict):
        return
    status = error.get("status")
    if status == 404:
        if player_scope:
            raise PlayerNotFound("没有找到该玩家，请检查昵称是否完整。", status=404)
        raise UpstreamError("DAK.GG 没有找到相关数据。", status=404)
    if status:
        raise UpstreamError(error.get("message") or f"DAK.GG 返回错误（{status}）。")


def _retry_after(value: str | None) -> float:
    try:
        return min(10.0, max(1.0, float(value))) if value else 2.0
    except (TypeError, ValueError):
        return 2.0

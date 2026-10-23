"""永恒轮回小助手 · AstrBot 插件入口。

数据来自 DAK.GG（https://dak.gg/er）。本项目为纯 AI 制作，仅供学习交流；
如涉及侵权请联系 QQ 邮箱 3033980089@qq.com。UI 只参考 DAK.GG 官网。
"""

from __future__ import annotations

import asyncio
import math
import time
import unicodedata

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.api import DakGGClient, DakGGError, PlayerNotFound, RateLimited, UpstreamError
from .core.assets import AssetCache
from .core.cards import CardRenderer
from .core.formatters import (
    format_character_markdown,
    format_character_stats_markdown,
    format_character_stats_text,
    format_character_text,
    format_character_view_markdown,
    format_character_view_text,
    format_eternal_score_markdown,
    format_eternal_score_text,
    format_item_markdown,
    format_item_text,
    format_match_history_markdown,
    format_match_history_text,
    format_player_markdown,
    format_player_text,
    format_skin_view_markdown,
    format_skin_view_text,
    format_tier_markdown,
    format_tier_text,
    help_text,
)
from .core.models import RANK_TIER_QUERY, parse_matching_mode_strict
from .core.queries import EternalReturnService
from .core.webpage import DakGGWebCapture

PLUGIN_VERSION = "2.2.1"

_CJK_RANGES = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x3040, 0x30FF),
    (0xAC00, 0xD7AF),
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def is_valid_nickname(nickname: str) -> bool:
    """复刻 EternalReturnLoMu 的昵称合法性校验。"""
    if not nickname or not nickname.strip():
        return False
    ascii_alnum = all(char.isascii() and char.isalnum() for char in nickname)
    if ascii_alnum:
        return 3 <= len(nickname) <= 16
    cjk_only = all(_is_cjk(char) for char in nickname)
    if cjk_only:
        return 2 <= len(nickname) <= 8
    mixed = all((char.isascii() and char.isalnum()) or _is_cjk(char) for char in nickname)
    return mixed and 2 <= len(nickname) <= 16


def hide_nickname(nickname: str) -> str:
    """隐藏昵称中间部分，用于错误提示。"""
    length = len(nickname)
    if length < 2:
        return nickname
    if length < 3:
        return f"{nickname[0]} * "
    return nickname[0] + " * " * (length - 2) + nickname[-1]


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


class EternalReturnHelperPlugin(Star):
    """永恒轮回查询与数据卡片插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self.client: DakGGClient | None = None
        self.service: EternalReturnService | None = None
        self.assets: AssetCache | None = None
        self.renderer: CardRenderer | None = None
        self._web_capture: DakGGWebCapture | None = None
        self._web_warned = False
        self._last_query: dict[str, float] = {}

    # ------------------------------------------------------------------ 生命周期
    async def initialize(self) -> None:
        self._ensure_service()
        if self.client:
            await self.client.start()
        self._ensure_renderer()
        if self.assets:
            await self.assets.start()
        logger.info("永恒轮回小助手已加载，数据来源 DAK.GG。")

    async def terminate(self) -> None:
        if self.service:
            await self.service.close()
        if self.client:
            await self.client.close()
        if self.assets:
            await self.assets.close()
        if self._web_capture:
            await self._web_capture.close()
        self.client = None
        self.service = None
        self.assets = None
        self.renderer = None
        self._web_capture = None
        logger.info("永恒轮回小助手已卸载。")

    # ------------------------------------------------------------------ 配置与工具
    def _cfg(self, key: str, default):
        try:
            value = self.config.get(key, default)
        except AttributeError:
            return default
        return default if value is None else value

    def _ensure_service(self) -> EternalReturnService:
        if self.service is None:
            self.client = DakGGClient(
                api_base=str(self._cfg("api_base", "https://er.dakgg.io/api")).strip()
                or "https://er.dakgg.io/api",
                timeout=float(self._cfg("request_timeout", 20) or 20),
                proxy=str(self._cfg("proxy", "") or ""),
                cache_ttl=int(self._cfg("cache_ttl", 300) or 0),
            )
            self.service = EternalReturnService(self.client, dict(self.config or {}))
        return self.service

    def _ensure_renderer(self) -> CardRenderer | None:
        if self.renderer is None:
            self.assets = AssetCache(
                timeout=float(self._cfg("request_timeout", 20) or 20),
                proxy=str(self._cfg("proxy", "") or ""),
            )
            self.renderer = CardRenderer(self.assets)
        return self.renderer

    def _ensure_web_capture(self) -> DakGGWebCapture:
        if self._web_capture is None:
            self._web_capture = DakGGWebCapture(timeout=int(self._cfg("webpage_timeout", 60) or 60))
        return self._web_capture

    async def _webpage_path(self, nickname: str, tab: str, matching_mode: int) -> str | None:
        if not self._cfg("webpage_screenshot", True):
            return None
        if str(self._cfg("render_mode", "卡片") or "卡片") == "文本":
            return None
        try:
            return await self._ensure_web_capture().capture(
                nickname, tab=tab, matching_mode=matching_mode
            )
        except Exception as exc:  # noqa: BLE001 - 截图失败回退卡片
            if not self._web_warned:
                logger.warning(f"官网截图失败，已回退卡片/文本：{exc}")
                self._web_warned = True
            return None

    def _prefix(self, event: AstrMessageEvent | None = None) -> str:
        try:
            config = self._get_context_config()
            if hasattr(config, "get"):
                wake = config.get("wake_prefix")
                if isinstance(wake, str) and wake.strip():
                    return wake.strip()
                if isinstance(wake, list) and wake:
                    return str(wake[0]).strip() or "/"
        except Exception:  # noqa: BLE001 - 仅影响帮助文案
            pass
        return "/"

    @staticmethod
    def _mode_error(mode: str, allowed: set[int]) -> str | None:
        if not mode:
            return None
        value = parse_matching_mode_strict(mode)
        if value is None or value not in allowed:
            names = " / ".join(
                {0: "全部", 2: "匹配", 3: "排位", 6: "钴协议"}[item] for item in sorted(allowed)
            )
            return f"不支持的模式「{mode}」，可选：{names}。"
        return None

    @staticmethod
    def _command_argument(event: AstrMessageEvent) -> str:
        """取出指令名之后的完整参数（用于支持带空格的装备名）。"""
        text = str(getattr(event, "message_str", "") or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    def _cooldown_left(self, event: AstrMessageEvent) -> int:
        seconds = int(self._cfg("cooldown_seconds", 3) or 0)
        if seconds <= 0:
            return 0
        key = f"{event.get_platform_id()}:{event.get_sender_id()}"
        now = time.monotonic()
        last = self._last_query.get(key)
        if last is not None:
            left = seconds - (now - last)
            if left > 0:
                return max(1, math.ceil(left))
        self._last_query[key] = now
        if len(self._last_query) > 2000:
            cutoff = now - seconds
            self._last_query = {k: v for k, v in self._last_query.items() if v >= cutoff}
        return 0

    async def _send(
        self,
        event: AstrMessageEvent,
        text: str,
        markdown: str,
        card=None,
    ):
        """按配置发送美术卡片 / 文转图 / 文本；失败时逐级回退。"""
        render_mode = str(self._cfg("render_mode", "卡片") or "卡片")
        if render_mode in ("卡片", "自动") and card is not None:
            try:
                renderer = self._ensure_renderer()
                if renderer is not None:
                    path = await card()
                    if path:
                        yield event.image_result(path)
                        return
            except Exception as exc:  # noqa: BLE001 - 卡片失败回退
                logger.warning(f"卡片渲染失败，尝试文转图或文本：{exc}")
            if render_mode == "卡片":
                try:
                    path = await self.text_to_image(markdown, return_url=False)
                    if path:
                        yield event.image_result(path)
                        return
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"文转图失败，回退为文本：{exc}")
                yield event.plain_result(text)
                return
        if render_mode == "卡片" and card is None:
            try:
                path = await self.text_to_image(markdown, return_url=False)
                if path:
                    yield event.image_result(path)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"文转图失败，回退为文本：{exc}")
            yield event.plain_result(text)
            return
        if render_mode in ("图片", "自动"):
            try:
                path = await self.text_to_image(markdown, return_url=False)
                if path:
                    yield event.image_result(path)
                    return
            except Exception as exc:  # noqa: BLE001 - 渲染失败必须回退
                logger.warning(f"文转图失败，回退为文本：{exc}")
        yield event.plain_result(text)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        if isinstance(exc, PlayerNotFound):
            return "没有找到该玩家，请检查昵称是否完整（DAK.GG 区分大小写）。"
        if isinstance(exc, RateLimited):
            return exc.message
        if isinstance(exc, UpstreamError):
            return exc.message
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "请求 DAK.GG 超时，请稍后再试。"
        if isinstance(exc, DakGGError):
            return exc.message
        return "查询失败，请稍后再试。"

    # ------------------------------------------------------------------ 绑定
    def _bind_key(self, event: AstrMessageEvent) -> str:
        return f"bind:{event.get_platform_id()}:{event.get_sender_id()}"

    async def _get_binding(self, event: AstrMessageEvent) -> str | None:
        try:
            value = await self.get_kv_data(self._bind_key(event), None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取绑定失败：{exc}")
            return None
        return str(value) if value else None

    @filter.command("绑定昵称", alias={"er绑定", "永恒绑定"})
    async def bind_player(self, event: AstrMessageEvent, nickname: str = ""):
        """绑定永恒轮回昵称，查询时无需重复输入。"""
        if not self._cfg("enable_binding", True):
            yield event.plain_result("昵称绑定功能已在插件配置中关闭。")
            return
        nickname = _normalize_text(nickname)
        if not nickname:
            yield event.plain_result("用法：/绑定昵称 <永恒轮回昵称>\n例如：/绑定昵称 RIOORI")
            return
        if not is_valid_nickname(nickname):
            yield event.plain_result(
                f"[{hide_nickname(nickname)}] 该名称不合法，EternalReturn 不允许使用这样的名称。"
            )
            return
        try:
            await self.put_kv_data(self._bind_key(event), nickname)
        except Exception as exc:  # noqa: BLE001
            logger.exception("保存绑定失败")
            yield event.plain_result(f"绑定失败：{exc}")
            return
        yield event.plain_result(
            f"已绑定昵称：{nickname}\n之后可以直接发送 /查询玩家 或 /查询玩家 我。"
        )

    @filter.command("解绑", alias={"解除绑定"})
    async def unbind_player(self, event: AstrMessageEvent):
        """解除昵称绑定。"""
        binding = await self._get_binding(event)
        if not binding:
            yield event.plain_result("你还没有绑定昵称。")
            return
        try:
            await self.delete_kv_data(self._bind_key(event))
        except Exception as exc:  # noqa: BLE001
            logger.exception("删除绑定失败")
            yield event.plain_result(f"解绑失败：{exc}")
            return
        yield event.plain_result(f"已解除绑定：{binding}")

    @filter.command("我的绑定", alias={"我的昵称", "查询绑定"})
    async def show_binding(self, event: AstrMessageEvent):
        """查看当前绑定的永恒轮回昵称。"""
        binding = await self._get_binding(event)
        if binding:
            yield event.plain_result(f"当前绑定：{binding}")
        else:
            yield event.plain_result("你还没有绑定昵称。发送 /绑定昵称 <昵称> 即可绑定。")

    async def _resolve_view_nickname(
        self, event: AstrMessageEvent, nickname: str
    ) -> tuple[str, str | None]:
        nickname = _normalize_text(nickname)
        if not nickname or nickname == "我":
            if not self._cfg("enable_binding", True):
                return "", "昵称绑定功能已关闭，请直接提供玩家昵称。"
            binding = await self._get_binding(event)
            if not binding:
                return "", "请提供玩家昵称，或先使用 /绑定昵称 <昵称> 绑定自己的昵称。"
            nickname = binding
        if not is_valid_nickname(nickname):
            return "", (
                f"[{hide_nickname(nickname)}] 该名称不合法，EternalReturn 不允许使用这样的名称。"
            )
        return nickname, None

    # ------------------------------------------------------------------ 玩家
    @filter.command("查询玩家", alias={"查玩家", "永恒查询"})
    async def query_player(
        self,
        event: AstrMessageEvent,
        nickname: str = "",
        mode: str = "",
        view: str = "",
    ):
        """查询永恒轮回玩家战绩、段位与最近对局。"""
        nickname = _normalize_text(nickname)
        if not nickname or nickname == "我":
            if not self._cfg("enable_binding", True):
                yield event.plain_result(
                    "昵称绑定功能已关闭，请直接提供玩家昵称。\n例如：/查询玩家 RIOORI 排位"
                )
                return
            binding = await self._get_binding(event)
            if not binding:
                yield event.plain_result(
                    "请提供玩家昵称，或先使用 /绑定昵称 <昵称> 绑定自己的昵称。\n"
                    "例如：/查询玩家 RIOORI 排位"
                )
                return
            nickname = binding

        if not is_valid_nickname(nickname):
            yield event.plain_result(
                f"[{hide_nickname(nickname)}] 该名称不合法，EternalReturn 不允许使用这样的名称。"
            )
            return
        mode = _normalize_text(mode)
        view = _normalize_text(view)
        page_aliases = {"实验体", "皮肤", "皮肤统计", "历史对局", "战绩"}
        if mode in page_aliases:
            mode, view = view, mode
        if view:
            if view == "实验体":
                target = self.character_view(event, nickname, mode)
            elif view in ("皮肤", "皮肤统计"):
                target = self.skin_view(event, nickname, mode)
            elif view in ("历史对局", "战绩"):
                target = self.match_history(event, nickname, "", mode)
            else:
                yield event.plain_result("未知的个人页，可选：实验体 / 历史对局 / 皮肤统计。")
                return
            async for result in target:
                yield result
            return

        mode_error = self._mode_error(mode, {0, 2, 3, 6})
        if mode_error:
            yield event.plain_result(mode_error)
            return

        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return

        web_mode = parse_matching_mode_strict(mode) if mode else 3
        web = await self._webpage_path(nickname, "overview", web_mode or 3)
        if web:
            yield event.image_result(web)
            return

        service = self._ensure_service()
        try:
            report = await service.player_report(
                nickname,
                mode,
                recent_limit=int(self._cfg("recent_matches", 6) or 0),
            )
        except Exception as exc:  # noqa: BLE001 - 统一回显
            if not isinstance(exc, DakGGError):
                logger.exception("查询玩家失败")
            yield event.plain_result(self._error_text(exc))
            return

        async for result in self._send(
            event,
            format_player_text(report),
            format_player_markdown(report),
            lambda: self.renderer.player(report),
        ):
            yield result

    # ------------------------------------------------------------------ 角色
    @filter.command("查询角色", alias={"查角色", "角色查询"})
    async def query_character(
        self,
        event: AstrMessageEvent,
        name: str = "",
        mode: str = "",
    ):
        """查询角色评级、武器流派、技能加点、出装与高分玩家。"""
        name = _normalize_text(name)
        if not name:
            yield event.plain_result(
                "请指定角色名，例如：/查询角色 杰琪\n可先使用 /角色数据 查看当前版本角色评级。"
            )
            return
        mode = _normalize_text(mode)
        mode_error = self._mode_error(mode, {2, 3, 6})
        if mode_error:
            yield event.plain_result(mode_error)
            return

        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return

        service = self._ensure_service()
        try:
            report = await service.character_detail(name, mode)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询角色失败")
            yield event.plain_result(self._error_text(exc))
            return

        async for result in self._send(
            event,
            format_character_text(report),
            format_character_markdown(report),
            lambda: self.renderer.character(report),
        ):
            yield result

    @filter.command("角色数据", alias={"角色排行", "英雄数据"})
    async def character_stats(
        self,
        event: AstrMessageEvent,
        mode: str = "",
        tier: str = "",
    ):
        """查看当前段位下全角色各武器的评级、胜率与平均伤害。"""
        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return

        mode = _normalize_text(mode)
        tier = _normalize_text(tier)
        if mode in RANK_TIER_QUERY and not tier:
            tier, mode = mode, ""
        if mode and (parse_matching_mode_strict(mode) != 3):
            yield event.plain_result(
                "DAK.GG 目前只提供排位模式的角色数据，请去掉模式参数或使用「排位」。"
            )
            return

        service = self._ensure_service()
        try:
            report = await service.character_stats_report("排位", tier)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询角色数据失败")
            yield event.plain_result(self._error_text(exc))
            return

        limit = int(self._cfg("character_rows", 30) or 30)
        async for result in self._send(
            event,
            format_character_stats_text(report, limit),
            format_character_stats_markdown(report, max(limit, 30)),
            lambda: self.renderer.character_stats(report, limit),
        ):
            yield result

    # ------------------------------------------------------------------ 个人页
    @filter.command("查询实验体", alias={"实验体"})
    async def character_view(self, event: AstrMessageEvent, nickname: str = "", mode: str = ""):
        """查看玩家全部实验体的场次、胜率、伤害与 RP。"""
        nickname, error = await self._resolve_view_nickname(event, nickname)
        if error:
            yield event.plain_result(error)
            return
        mode = _normalize_text(mode)
        mode_error = self._mode_error(mode, {0, 2, 3, 6})
        if mode_error:
            yield event.plain_result(mode_error)
            return
        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return
        web_mode = parse_matching_mode_strict(mode) if mode else 3
        web = await self._webpage_path(nickname, "character", web_mode or 3)
        if web:
            yield event.image_result(web)
            return

        service = self._ensure_service()
        try:
            report = await service.character_view(nickname, mode)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询实验体失败")
            yield event.plain_result(self._error_text(exc))
            return
        async for result in self._send(
            event,
            format_character_view_text(report, 50),
            format_character_view_markdown(report, 60),
            lambda: self.renderer.character_view(report),
        ):
            yield result

    @filter.command("查询皮肤", alias={"皮肤统计"})
    async def skin_view(self, event: AstrMessageEvent, nickname: str = "", mode: str = ""):
        """查看玩家皮肤使用次数、胜率与伤害。"""
        nickname, error = await self._resolve_view_nickname(event, nickname)
        if error:
            yield event.plain_result(error)
            return
        mode = _normalize_text(mode)
        mode_error = self._mode_error(mode, {0, 2, 3, 6})
        if mode_error:
            yield event.plain_result(mode_error)
            return
        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return
        web_mode = parse_matching_mode_strict(mode) if mode else 3
        web = await self._webpage_path(nickname, "skins", web_mode or 3)
        if web:
            yield event.image_result(web)
            return

        service = self._ensure_service()
        try:
            report = await service.skin_view(nickname, mode)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询皮肤失败")
            yield event.plain_result(self._error_text(exc))
            return
        async for result in self._send(
            event,
            format_skin_view_text(report, 40),
            format_skin_view_markdown(report, 40),
            lambda: self.renderer.skin_view(report),
        ):
            yield result

    @filter.command("历史对局", alias={"对局记录"})
    async def match_history(
        self,
        event: AstrMessageEvent,
        nickname: str = "",
        page: str = "",
        mode: str = "",
    ):
        """查看玩家历史对局，可指定页码与模式。"""
        nickname, error = await self._resolve_view_nickname(event, nickname)
        if error:
            yield event.plain_result(error)
            return
        page = _normalize_text(page)
        mode = _normalize_text(mode)
        if page and not page.isdigit():
            if not mode and parse_matching_mode_strict(page) is not None:
                mode, page = page, ""
            else:
                yield event.plain_result("页码必须是数字，例如：/历史对局 RIOORI 2 排位")
                return
        page_number = max(1, int(page)) if page.isdigit() else 1
        mode_error = self._mode_error(mode, {0, 2, 3, 6})
        if mode_error:
            yield event.plain_result(mode_error)
            return
        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return
        if page_number == 1:
            web_mode = parse_matching_mode_strict(mode) if mode else 3
            web = await self._webpage_path(nickname, "matches", web_mode or 3)
            if web:
                yield event.image_result(web)
                return

        service = self._ensure_service()
        try:
            report = await service.match_history(nickname, mode, page_number)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询历史对局失败")
            yield event.plain_result(self._error_text(exc))
            return
        async for result in self._send(
            event,
            format_match_history_text(report),
            format_match_history_markdown(report),
            lambda: self.renderer.match_history(report),
        ):
            yield result

    # ------------------------------------------------------------------ 段位
    @filter.command("段位统计", alias={"段位分布"})
    async def tier_statistics(self, event: AstrMessageEvent, server: str = ""):
        """查看全服段位人数分布与永恒/半神分数线。"""
        service = self._ensure_service()
        try:
            report = await service.tier_report(server)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询段位统计失败")
            yield event.plain_result(self._error_text(exc))
            return

        async for result in self._send(
            event,
            format_tier_text(report),
            format_tier_markdown(report),
            lambda: self.renderer.tier(report),
        ):
            yield result

    @filter.command("永恒多少分", alias={"分数线"})
    async def eternal_score(self, event: AstrMessageEvent, server: str = ""):
        """查询永恒/半神当前分数线与最高 RP。"""
        service = self._ensure_service()
        try:
            report = await service.eternal_score_report(server)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询分数线失败")
            yield event.plain_result(self._error_text(exc))
            return

        async for result in self._send(
            event,
            format_eternal_score_text(report),
            format_eternal_score_markdown(report),
            lambda: self.renderer.score(report),
        ):
            yield result

    # ------------------------------------------------------------------ 装备
    @filter.command("查询装备", alias={"装备查询", "查装备"})
    async def query_item(self, event: AstrMessageEvent):
        """查询装备品质、属性说明与合成材料。"""
        keyword = _normalize_text(self._command_argument(event))
        if not keyword:
            yield event.plain_result("请指定装备名、拼音或装备 ID，例如：/查询装备 军刀")
            return

        left = self._cooldown_left(event)
        if left:
            yield event.plain_result(f"查询冷却中，请 {left} 秒后再试。")
            return

        service = self._ensure_service()
        try:
            result = await service.item_query(keyword)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, DakGGError):
                logger.exception("查询装备失败")
            yield event.plain_result(self._error_text(exc))
            return
        if result.item is None:
            yield event.plain_result(format_item_text(result))
            return

        async for item_result in self._send(
            event,
            format_item_text(result),
            format_item_markdown(result),
            lambda: self.renderer.item(result),
        ):
            yield item_result

    # ------------------------------------------------------------------ 帮助
    @filter.command(
        "永恒轮回帮助",
        alias={"er帮助", "erhelp", "助手帮助", "永恒帮助"},
    )
    async def show_help(self, event: AstrMessageEvent):
        """查看永恒轮回插件的全部指令。"""
        yield event.plain_result(help_text(self._prefix(event), version=PLUGIN_VERSION))

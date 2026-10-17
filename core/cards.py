"""本地美术卡片渲染。

参考 DAK.GG 网页与原项目公开截图的视觉语言，用 Pillow 独立实现的卡片渲染：
深色页头、白色面板、深色表头、金色强调色，以及角色/武器/装备图标。
所有图片来自 DAK.GG CDN，异步下载并缓存。
"""

from __future__ import annotations

import asyncio
import io
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from .assets import AssetCache
from .queries import (
    CharacterDetail,
    CharacterStatsReport,
    ItemQueryResult,
    PlayerReport,
    TierReport,
    server_label,
)

W = 1290
M = 20
BG = (245, 245, 245)
WHITE = (255, 255, 255)
INK = (28, 30, 36)
MUTED = (100, 100, 100)
LINE = (230, 230, 230)
DARK = (22, 22, 24)
DARKER = (22, 22, 24)
TABLE_HEAD = (54, 57, 68)
GOLD = (224, 149, 0)
ORANGE = (224, 149, 0)
GREEN = (17, 178, 136)
BLUE = (83, 147, 202)
RED = (255, 70, 85)
PURPLE = (71, 84, 130)
GRADE_COLORS = {
    "S": (255, 128, 64),
    "A": (255, 196, 64),
    "B": (96, 186, 255),
    "C": (150, 160, 176),
    "D": (120, 126, 138),
}

_FONT_CANDIDATES_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyhbd.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]
_FONT_BOLD = next((p for p in _FONT_CANDIDATES_BOLD if os.path.exists(p)), None)
_FONT_REGULAR = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), _FONT_BOLD)
_FONT_KO = next(
    (
        p
        for p in (
            r"C:\Windows\Fonts\malgun.ttf",
            r"C:\Windows\Fonts\gulim.ttc",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        )
        if os.path.exists(p)
    ),
    _FONT_REGULAR,
)
_FONT_KO_BOLD = next(
    (
        p
        for p in (
            r"C:\Windows\Fonts\malgunbd.ttf",
            r"C:\Windows\Fonts\gulimb.ttc",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        )
        if os.path.exists(p)
    ),
    _FONT_KO,
)
_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
_KO_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (max(8, int(size)), bold)
    if key not in _FONT_CACHE:
        path = _FONT_BOLD if bold else _FONT_REGULAR
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, key[0])
        except (OSError, TypeError):
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _is_hangul(char: str) -> bool:
    code = ord(char)
    return 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F


def ko_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (max(8, int(size)), bold)
    if key not in _KO_CACHE:
        try:
            _KO_CACHE[key] = ImageFont.truetype(_FONT_KO_BOLD if bold else _FONT_KO, key[0])
        except (OSError, TypeError):
            _KO_CACHE[key] = font(size, bold)
    return _KO_CACHE[key]


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    value: str,
    size: int,
    fill=INK,
    bold: bool = False,
    anchor: str = "la",
) -> None:
    value = str(value)
    if not any(_is_hangul(ch) for ch in value):
        draw.text(xy, value, font=font(size, bold), fill=fill, anchor=anchor)
        return

    # 中文字体不含韩文字形：按文字类型拆分后分别绘制
    runs: list[tuple[str, bool]] = []
    for char in value:
        hangul = _is_hangul(char)
        if runs and runs[-1][1] == hangul:
            runs[-1] = (runs[-1][0] + char, hangul)
        else:
            runs.append((char, hangul))

    widths = [
        draw.textlength(run, font=ko_font(size, bold) if hangul else font(size, bold))
        for run, hangul in runs
    ]
    total = sum(widths)
    x, y = xy
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    vertical = anchor[1] if len(anchor) > 1 else "a"
    for (run, hangul), width in zip(runs, widths, strict=False):
        draw.text(
            (x, y),
            run,
            font=ko_font(size, bold) if hangul else font(size, bold),
            fill=fill,
            anchor=f"l{vertical}",
        )
        x += width


def fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    size: int,
    max_width: int,
    bold: bool = False,
) -> str:
    value = str(value)
    if draw.textlength(value, font=font(size, bold)) <= max_width:
        return value
    ellipsis = "…"
    while value and draw.textlength(value + ellipsis, font=font(size, bold)) > max_width:
        value = value[:-1]
    return value + ellipsis if value else ellipsis


def wrap_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    size: int,
    max_width: int,
    max_lines: int = 3,
    bold: bool = False,
) -> list[str]:
    fnt = font(size, bold)
    lines: list[str] = []
    current = ""
    for char in str(value):
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        if draw.textlength(current + char, font=fnt) > max_width:
            lines.append(current)
            current = char
        else:
            current += char
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        lines = [""]
    if len(lines) == max_lines and len("".join(lines)) < len(str(value)):
        last = lines[-1]
        while draw.textlength(last + "…", font=fnt) > max_width and last:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill,
    outline=None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def shadowed_card(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 6,
    fill=WHITE,
) -> None:
    """网页风格面板：白底 + 细边框，不使用投影。"""
    draw = ImageDraw.Draw(canvas)
    rounded(draw, box, radius, fill, LINE)


def _open_image(blob: bytes | None) -> Image.Image | None:
    if not blob:
        return None
    try:
        return Image.open(io.BytesIO(blob)).convert("RGBA")
    except (OSError, ValueError):
        return None


def _cover(image: Image.Image | None, size: tuple[int, int]) -> Image.Image:
    if image is None:
        return Image.new("RGBA", size, (210, 214, 222, 255))
    return ImageOps.fit(image, size, method=Image.LANCZOS)


def _rounded_image(image: Image.Image | None, size: tuple[int, int], radius: int) -> Image.Image:
    fitted = _cover(image, size)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    alpha = ImageChops.multiply(fitted.getchannel("A"), mask)
    fitted.putalpha(alpha)
    return fitted


def _circle_image(image: Image.Image | None, size: int, fallback: str = "") -> Image.Image:
    fitted = _cover(image, (size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    fitted.putalpha(mask)
    if image is None and fallback:
        draw = ImageDraw.Draw(fitted)
        text(draw, (size / 2, size / 2), fallback[:1], int(size * 0.42), WHITE, True, "mm")
    return fitted


def icon_chip(
    canvas: Image.Image,
    blob: bytes | None,
    xy: tuple[int, int],
    size: int,
    radius: int = 8,
    bg=(52, 54, 62, 255),
) -> None:
    """深色底 + 透明武器图标。"""
    x, y = xy
    chip = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=bg)
    canvas.alpha_composite(chip, (x, y))
    image = _open_image(blob)
    if image is not None:
        inner = max(8, size - 8)
        canvas.alpha_composite(
            _rounded_image(image, (inner, inner), max(2, radius - 2)),
            (x + (size - inner) // 2, y + (size - inner) // 2),
        )


def _grade_color(grade: str):
    return GRADE_COLORS.get(str(grade).upper(), GRADE_COLORS["D"])


def _rank_color(rank: int):
    if rank == 1:
        return GREEN
    if rank in (2, 3):
        return BLUE
    if rank == 99:
        return PURPLE
    return (150, 156, 168)


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class CardRenderer:
    """把查询结果绘制为 PNG 卡片。"""

    def __init__(self, assets: AssetCache) -> None:
        self.assets = assets
        self.output_dir = Path(tempfile.gettempdir()) / "astrbot_er_lomu_cards"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def _blobs(self, urls: list[str | None]) -> dict[str, bytes]:
        return await self.assets.get_many(urls)

    def _save(self, image: Image.Image, name: str) -> str:
        path = self.output_dir / f"{name}-{int(datetime.now().timestamp() * 1000)}.png"
        image.convert("RGB").save(path, "PNG")
        return str(path)

    # ------------------------------------------------------------------ 玩家
    async def player(self, report: PlayerReport) -> str:
        urls: list[str | None] = [report.banner, report.avatar, report.tier_icon]
        urls += [use.image for use in report.characters]
        urls += [mate.image for mate in report.duo]
        for match in report.matches:
            urls += [match.character_image, match.weapon_icon, *match.item_icons]
        blobs = await self._blobs(urls)
        return await asyncio.to_thread(self._draw_player, report, blobs)

    def _draw_player(self, report: PlayerReport, blobs: dict[str, bytes]) -> str:
        nav_bottom = 64
        hero_bottom = 300
        tab_bottom = 362
        body_top = 380
        left_x, left_w = M, 850
        right_x, right_w = 890, W - M - 890
        match_rows = len(report.matches)
        match_h = 40 + match_rows * 56
        char_h = 40 + max(1, min(5, len(report.characters))) * 46
        duo_h = (40 + min(3, len(report.duo)) * 46) if report.duo else 0
        right_h = 300 + 20 + char_h + (20 + duo_h if duo_h else 0)
        left_h = 110 + 12 + match_h + 20 + 220
        height = body_top + max(left_h, right_h) + 50
        canvas = Image.new("RGBA", (W, height), BG)
        draw = ImageDraw.Draw(canvas)

        # 顶部导航
        rounded(draw, (M, 16, W - M, nav_bottom), 6, DARK)
        text(draw, (M + 24, 40), "ER", 20, GOLD, True, "lm")
        text(draw, (M + 58, 40), "永恒轮回小助手", 18, WHITE, True, "lm")
        for index, label in enumerate(("概览", "战绩", "角色", "排行")):
            nx = M + 250 + index * 74
            color = WHITE if index == 0 else (155, 158, 168)
            text(draw, (nx, 40), label, 14, color, index == 0, "lm")
            if index == 0:
                draw.rectangle((nx - 8, nav_bottom - 5, nx + 34, nav_bottom - 2), fill=GOLD)
        search_box = (W - M - 230, 28, W - M - 18, 52)
        rounded(draw, search_box, 4, (38, 38, 42), (70, 72, 80))
        text(
            draw,
            (search_box[0] + 12, 40),
            fit_text(draw, report.nickname, 13, 190),
            13,
            (210, 212, 218),
            anchor="lm",
        )

        # 深色头图
        hero = (M, 76, W - M, hero_bottom)
        rounded(draw, hero, 6, DARK)
        banner = _open_image(blobs.get(report.banner or "")) or _open_image(
            blobs.get(report.avatar or "")
        )
        if banner is not None:
            art_w = min(620, max(1, int(banner.width / max(1, banner.height) * 224)))
            art = ImageOps.fit(banner, (art_w, 224), method=Image.LANCZOS)
            canvas.alpha_composite(art, (hero[2] - art_w, 76))
        overlay = Image.new("RGBA", (hero[2] - hero[0], 224), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        width = overlay.size[0]
        for x in range(width):
            alpha = int(245 - 215 * (x / width))
            odraw.line([(x, 0), (x, 224)], fill=(22, 22, 24, alpha))
        mask = Image.new("L", overlay.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, overlay.size[0], 224), radius=6, fill=255)
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
        canvas.alpha_composite(overlay, (M, 76))

        avatar = _circle_image(_open_image(blobs.get(report.avatar or "")), 116, report.nickname)
        border = Image.new("RGBA", (124, 124), (0, 0, 0, 0))
        ImageDraw.Draw(border).ellipse((0, 0, 123, 123), fill=(255, 255, 255, 235))
        canvas.alpha_composite(border, (M + 34, 104))
        canvas.alpha_composite(avatar, (M + 38, 108))

        text(draw, (M + 178, 100), report.nickname, 38, WHITE, True)
        chip = f"Lv.{report.level}"
        cw = int(draw.textlength(chip, font=font(13, True))) + 20
        rounded(draw, (M + 180, 152, M + 180 + cw, 176), 4, (38, 38, 42), (78, 80, 90))
        text(draw, (M + 180 + cw / 2, 164), chip, 13, WHITE, True, "mm")
        text(
            draw,
            (M + 190 + cw, 164),
            f"{report.mode_name} · {report.season_name}",
            13,
            (200, 203, 212),
            anchor="lm",
        )

        tier_icon = _open_image(blobs.get(report.tier_icon or ""))
        if tier_icon is not None:
            canvas.alpha_composite(_rounded_image(tier_icon, (58, 58), 8), (M + 180, 196))
        if report.mode_id == 3:
            text(draw, (M + 252, 198), report.tier, 24, WHITE, True)
            text(draw, (M + 252, 230), f"{report.mmr:,} MMR", 20, GOLD, True)
            rank = ""
            if report.rank_global:
                rank = f"第 {report.rank_global} 名"
            elif report.rank_local:
                rank = f"本服第 {report.rank_local} 名"
            if rank:
                text(draw, (M + 400, 238), rank, 13, (200, 203, 212))
        else:
            text(draw, (M + 252, 198), "非排位数据", 22, WHITE, True)
            text(draw, (M + 252, 230), report.mode_name, 16, GOLD)

        if report.rating:
            score = f"（{report.rating.score:.2f}x）" if report.rating.score is not None else ""
            text(
                draw,
                (M + 38, 250),
                f"表现评分 {report.rating.grade}{score} · {report.rating.message}",
                14,
                GOLD,
                True,
            )

        # 标签栏
        rounded(draw, (M, 312, W - M, tab_bottom), 0, WHITE)
        draw.line((M, tab_bottom, W - M, tab_bottom), fill=LINE, width=1)
        tabs = ["概览", "战绩", "角色", "双排"]
        for index, label in enumerate(tabs):
            tx = M + 26 + index * 86
            color = INK if index == 0 else MUTED
            text(draw, (tx, 336), label, 15, color, index == 0)
            if index == 0:
                draw.rectangle((tx - 8, tab_bottom - 3, tx + 34, tab_bottom), fill=GOLD)
        updated = getattr(report, "updated_at", None)
        if updated:
            text(draw, (W - M - 20, 336), f"更新于 {updated}", 11, MUTED, anchor="ra")

        # 左侧概要条
        self._draw_summary_bar(canvas, report, (left_x, body_top, left_x + left_w, body_top + 110))
        self._draw_matches_table(canvas, report, (left_x, body_top + 122), blobs)
        self._draw_mmr_panel(
            canvas,
            report,
            (
                left_x,
                body_top + 122 + match_h + 20,
                left_x + left_w,
                body_top + 122 + match_h + 240,
            ),
        )
        # 右侧
        self._draw_season_panel(
            canvas, report, (right_x, body_top, right_x + right_w, body_top + 300)
        )
        cy = body_top + 320
        self._draw_char_table(canvas, report, (right_x, cy), char_h, blobs)
        cy += char_h + 20
        if report.duo:
            self._draw_duo_table(canvas, report, (right_x, cy), duo_h, blobs)

        text(
            draw,
            (W - M, height - 22),
            f"数据来源 DAK.GG · 生成于 {_now_label()}",
            11,
            MUTED,
            anchor="ra",
        )
        return self._save(canvas, "player")

    def _draw_summary_bar(self, canvas, report: PlayerReport, box) -> None:
        draw = ImageDraw.Draw(canvas)
        rounded(draw, box, 4, WHITE, LINE)
        count = len(report.matches)
        values = [
            ("近期对局", f"{count} 场"),
            ("获胜", f"{sum(1 for m in report.matches if m.rank == 1)} 场"),
            (
                "平均排名",
                f"{sum(m.rank if m.rank != 99 else 8 for m in report.matches) / max(1, count):.1f}",
            ),
            ("平均伤害", f"{sum(m.damage for m in report.matches) / max(1, count):,.0f}"),
        ]
        for index, (label, value) in enumerate(values):
            cx = box[0] + 24 + index * 206
            if index:
                draw.line((cx - 18, box[1] + 22, cx - 18, box[3] - 22), fill=LINE)
            text(draw, (cx, box[1] + 30), label, 12, MUTED)
            text(draw, (cx, box[1] + 56), value, 24, INK, True)

    def _draw_matches_table(self, canvas, report: PlayerReport, origin, blobs) -> None:
        x, y = origin
        width = 850
        header_h, row_h = 40, 56
        draw = ImageDraw.Draw(canvas)
        cols = [
            ("排名", 26),
            ("模式", 86),
            ("角色", 150),
            ("TK", 366),
            ("K / D / A", 424),
            ("伤害", 538),
            ("RP", 630),
            ("KDA", 700),
            ("装备", 776),
        ]
        rounded(draw, (x, y, x + width, y + header_h), 4, TABLE_HEAD)
        draw.rectangle((x, y + header_h - 4, x + width, y + header_h), fill=TABLE_HEAD)
        for label, offset in cols:
            text(draw, (x + offset, y + 20), label, 11, WHITE, True, "lm")
        for index, match in enumerate(report.matches):
            ry = y + header_h + index * row_h
            draw.rectangle((x, ry, x + width, ry + row_h), fill=WHITE)
            draw.line((x, ry + row_h, x + width, ry + row_h), fill=LINE)
            color = _rank_color(match.rank)
            rank_text = "逃离" if match.rank == 99 else f"#{match.rank}"
            text(draw, (x + 26, ry + 28), rank_text, 17, color, True, "lm")
            text(draw, (x + 86, ry + 28), match.mode, 12, MUTED, anchor="lm")
            avatar = _circle_image(
                _open_image(blobs.get(match.character_image or "")), 32, match.character
            )
            canvas.alpha_composite(avatar, (x + 150, ry + 12))
            text(
                draw,
                (x + 190, ry + 28),
                fit_text(draw, match.character, 13, 76, True),
                13,
                INK,
                True,
                "lm",
            )
            icon_chip(canvas, blobs.get(match.weapon_icon or ""), (x + 268, ry + 16), 24, 5)
            text(draw, (x + 366, ry + 28), str(match.team_kills), 14, INK, "lm")
            text(
                draw,
                (x + 424, ry + 28),
                f"{match.kills} / {match.deaths} / {match.assists}",
                14,
                INK,
                "lm",
            )
            text(draw, (x + 538, ry + 28), f"{match.damage:,}", 14, INK, "lm")
            gain_color = GREEN if match.mmr_gain > 0 else (RED if match.mmr_gain < 0 else MUTED)
            gain = f"+{match.mmr_gain}" if match.mmr_gain > 0 else str(match.mmr_gain)
            text(draw, (x + 630, ry + 28), gain, 14, gain_color, True, "lm")
            text(draw, (x + 700, ry + 28), f"{match.kda:.2f}", 14, INK, "lm")
            for slot, url in enumerate(match.item_icons[:5]):
                icon = _open_image(blobs.get(url))
                ix = x + 752 + slot * 19
                if icon is not None:
                    canvas.alpha_composite(_rounded_image(icon, (18, 18), 3), (ix, ry + 19))
        draw.rectangle(
            (x, y + header_h, x + width, y + header_h + len(report.matches) * row_h), outline=LINE
        )

    def _draw_season_panel(self, canvas, report: PlayerReport, box) -> None:
        draw = ImageDraw.Draw(canvas)
        rounded(draw, box, 4, WHITE, LINE)
        text(draw, (box[0] + 18, box[1] + 18), "赛季概览", 17, INK, True)
        stats = report.stats
        rows = [
            ("场次", f"{stats['play']:,}"),
            ("胜率", f"{stats['win_rate'] * 100:.2f}%"),
            ("TOP3", f"{stats['top3_rate'] * 100:.2f}%"),
            ("平均排名", f"#{stats['avg_rank']:.2f}"),
            ("平均击杀", f"{stats['avg_kills']:.2f}"),
            ("平均助攻", f"{stats['avg_assists']:.2f}"),
            ("平均TK", f"{stats['avg_team_kills']:.2f}"),
            ("平均伤害", f"{stats['avg_damage']:,}"),
        ]
        for index, (label, value) in enumerate(rows):
            ry = box[1] + 48 + index * 30
            if index:
                draw.line((box[0] + 18, ry, box[2] - 18, ry), fill=(244, 244, 244))
            text(draw, (box[0] + 18, ry + 15), label, 12, MUTED, anchor="lm")
            text(draw, (box[2] - 18, ry + 15), value, 13, INK, True, "rm")

    def _draw_mmr_panel(self, canvas, report: PlayerReport, box) -> None:
        draw = ImageDraw.Draw(canvas)
        rounded(draw, box, 4, WHITE, LINE)
        text(draw, (box[0] + 18, box[1] + 20), "MMR 走势", 14, INK, True)
        if not report.mmr_trend:
            text(draw, (box[0] + 18, box[1] + 60), "暂无 MMR 数据", 12, MUTED)
            return
        latest = report.mmr_trend[-1]["mmr"]
        text(draw, (box[2] - 18, box[1] + 20), f"当前 {latest:,} MMR", 12, GOLD, True, "ra")
        chart = (box[0] + 46, box[1] + 58, box[2] - 26, box[3] - 44)
        for line_index in range(4):
            ly = chart[1] + (chart[3] - chart[1]) * line_index / 3
            draw.line((chart[0], ly, chart[2], ly), fill=(238, 238, 238))
        points = [p["mmr"] for p in report.mmr_trend]
        low, high = min(points), max(points)
        step = (chart[2] - chart[0]) / max(1, len(points) - 1)
        coords = [
            (chart[0] + i * step, chart[3] - (v - low) / max(1, high - low) * (chart[3] - chart[1]))
            for i, v in enumerate(points)
        ]
        draw.line(coords, fill=GOLD, width=2)
        for index, point in enumerate(coords):
            draw.ellipse((point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3), fill=GOLD)
            text(
                draw,
                (point[0], box[3] - 28),
                report.mmr_trend[index]["date"],
                10,
                MUTED,
                anchor="ma",
            )
        text(draw, (chart[0] - 8, chart[1]), f"{high:,}", 10, MUTED, anchor="ra")
        text(draw, (chart[0] - 8, chart[3]), f"{low:,}", 10, MUTED, anchor="ra")

    def _draw_char_table(self, canvas, report: PlayerReport, origin, height, blobs) -> None:
        x, y = origin
        width = 380
        draw = ImageDraw.Draw(canvas)
        rounded(draw, (x, y, x + width, y + 40), 4, TABLE_HEAD)
        draw.rectangle((x, y + 36, x + width, y + 40), fill=TABLE_HEAD)
        text(draw, (x + 16, y + 20), "常用角色", 13, WHITE, True, "lm")
        text(draw, (x + 236, y + 20), "胜率", 11, WHITE, anchor="lm")
        text(draw, (x + 310, y + 20), "场次", 11, WHITE, anchor="lm")
        for index, use in enumerate(report.characters[:5]):
            ry = y + 40 + index * 46
            draw.rectangle((x, ry, x + width, ry + 46), fill=WHITE)
            draw.line((x, ry + 46, x + width, ry + 46), fill=LINE)
            avatar = _circle_image(_open_image(blobs.get(use.image or "")), 28, use.name)
            canvas.alpha_composite(avatar, (x + 14, ry + 9))
            text(
                draw,
                (x + 50, ry + 23),
                fit_text(draw, use.name, 13, 110, True),
                13,
                INK,
                True,
                "lm",
            )
            text(draw, (x + 236, ry + 23), f"{use.win_rate * 100:.1f}%", 13, INK, anchor="lm")
            text(draw, (x + 310, ry + 23), f"{use.plays}", 12, MUTED, anchor="lm")
        draw.rectangle((x, y, x + width, y + height), outline=LINE)

    def _draw_duo_table(self, canvas, report: PlayerReport, origin, height, blobs) -> None:
        x, y = origin
        width = 380
        draw = ImageDraw.Draw(canvas)
        rounded(draw, (x, y, x + width, y + 40), 4, TABLE_HEAD)
        draw.rectangle((x, y + 36, x + width, y + 40), fill=TABLE_HEAD)
        text(draw, (x + 16, y + 20), "一起游玩", 13, WHITE, True, "lm")
        text(draw, (x + 236, y + 20), "胜率", 11, WHITE, anchor="lm")
        text(draw, (x + 310, y + 20), "平均排名", 11, WHITE, anchor="lm")
        for index, mate in enumerate(report.duo[:3]):
            ry = y + 40 + index * 46
            draw.rectangle((x, ry, x + width, ry + 46), fill=WHITE)
            draw.line((x, ry + 46, x + width, ry + 46), fill=LINE)
            avatar = _circle_image(_open_image(blobs.get(mate.image or "")), 28, mate.name)
            canvas.alpha_composite(avatar, (x + 14, ry + 9))
            text(
                draw,
                (x + 50, ry + 23),
                fit_text(draw, mate.name, 12, 170, True),
                12,
                INK,
                True,
                "lm",
            )
            text(draw, (x + 236, ry + 23), f"{mate.win_rate * 100:.1f}%", 12, INK, anchor="lm")
            text(draw, (x + 310, ry + 23), f"#{mate.avg_rank:.1f}", 12, MUTED, anchor="lm")
        draw.rectangle((x, y, x + width, y + height), outline=LINE)

    # ------------------------------------------------------------------ 角色数据
    async def character_stats(self, report: CharacterStatsReport, limit: int = 60) -> str:
        rows = report.rows[: max(1, limit)]
        urls: list[str | None] = []
        for row in rows:
            urls += [row.character_image, row.weapon_icon]
        blobs = await self._blobs(urls)
        return await asyncio.to_thread(self._draw_character_stats, report, rows, blobs)

    def _draw_character_stats(
        self,
        report: CharacterStatsReport,
        rows: list,
        blobs: dict[str, bytes],
    ) -> str:
        cols, gap = 10, 12
        card_w = (W - 2 * M - (cols - 1) * gap) // cols
        card_h = 168
        row_count = max(1, math.ceil(len(rows) / cols))
        height = M + 112 + 16 + row_count * (card_h + gap) + 50
        canvas = Image.new("RGBA", (W, height), BG)
        draw = ImageDraw.Draw(canvas)
        shadowed_card(canvas, (M, M, W - M, M + 112))
        text(draw, (M + 26, M + 22), f"角色数据 · {report.tier_label}", 34, INK, True)
        meta = f"{report.mode_name} · {report.season_name} · {report.total_games:,} 场 · {report.total_players:,} 名玩家"
        if report.updated_at:
            meta += f" · 更新 {report.updated_at}"
        text(draw, (M + 26, M + 74), meta, 15, MUTED)

        start_y = M + 128
        for index, row in enumerate(rows):
            col, line = index % cols, index // cols
            x = M + col * (card_w + gap)
            y = start_y + line * (card_h + gap)
            box = (x, y, x + card_w, y + card_h)
            shadowed_card(canvas, box, 14)
            avatar = _circle_image(
                _open_image(blobs.get(row.character_image or "")), 64, row.character
            )
            canvas.alpha_composite(avatar, (x + (card_w - 64) // 2, y + 12))
            # 评级
            color = _grade_color(row.grade)
            draw.ellipse((x + 10, y + 10, x + 40, y + 40), fill=color)
            text(draw, (x + 25, y + 25), row.grade or "-", 16, WHITE, True, "mm")
            # 武器图标
            icon_chip(canvas, blobs.get(row.weapon_icon or ""), (x + card_w - 38, y + 12), 28, 7)
            text(
                draw,
                (x + card_w / 2, y + 88),
                fit_text(draw, row.character, 13, card_w - 12, True),
                13,
                INK,
                True,
                "ma",
            )
            text(
                draw,
                (x + card_w / 2, y + 110),
                f"胜率 {row.win_rate * 100:.2f}%",
                12,
                MUTED,
                anchor="ma",
            )
            text(
                draw, (x + card_w / 2, y + 130), f"伤害 {row.avg_damage:,}", 12, ORANGE, True, "ma"
            )
            text(draw, (x + card_w / 2, y + 150), f"出场 {row.plays:,}", 10, MUTED, anchor="ma")

        text(
            draw,
            (W - M, height - 28),
            f"数据来源 DAK.GG · 生成于 {_now_label()}",
            13,
            MUTED,
            anchor="ra",
        )
        return self._save(canvas, "character-stats")

    # ------------------------------------------------------------------ 角色详情
    async def character(self, report: CharacterDetail) -> str:
        urls: list[str | None] = [report.banner, report.profile_image, report.weapon_icon]
        for weapon in report.weapons:
            urls.append(weapon.get("icon"))
        for build in report.item_builds:
            urls.extend(build.get("images") or [])
        blobs = await self._blobs(urls)
        return await asyncio.to_thread(self._draw_character, report, blobs)

    def _draw_character(self, report: CharacterDetail, blobs: dict[str, bytes]) -> str:
        height = 1060
        canvas = Image.new("RGBA", (W, height), BG)
        draw = ImageDraw.Draw(canvas)

        # 头图
        banner_box = (M, M, W - M, M + 220)
        rounded(draw, banner_box, 20, DARKER)
        banner = _open_image(blobs.get(report.banner or "")) or _open_image(
            blobs.get(report.profile_image or "")
        )
        if banner is not None:
            target_h = 220
            art_w = max(1, min(760, int(banner.width / max(1, banner.height) * target_h)))
            art = ImageOps.fit(banner, (art_w, target_h), method=Image.LANCZOS)
            canvas.alpha_composite(art, (banner_box[2] - art_w, banner_box[1]))
        overlay = Image.new("RGBA", (banner_box[2] - banner_box[0], 220), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        width = overlay.size[0]
        for x in range(width):
            alpha = int(190 - 160 * (x / width))
            odraw.line([(x, 0), (x, 220)], fill=(14, 16, 22, alpha))
        mask = Image.new("L", overlay.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, overlay.size[0], 220), radius=20, fill=255)
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
        canvas.alpha_composite(overlay, (banner_box[0], banner_box[1]))

        avatar = _circle_image(_open_image(blobs.get(report.profile_image or "")), 160, report.name)
        canvas.alpha_composite(avatar, (M + 26, M + 30))
        text(draw, (M + 212, M + 42), report.name, 50, WHITE, True)
        text(
            draw,
            (M + 212, M + 112),
            " / ".join(report.archetypes) if report.archetypes else "未知定位",
            22,
            (220, 224, 232),
        )
        text(
            draw,
            (M + 212, M + 150),
            f"出场 {report.plays:,} · 胜率 {report.win_rate * 100:.1f}%",
            18,
            GOLD,
            True,
        )
        # 评级
        color = _grade_color(report.grade)
        cx = W - M - 90
        draw.ellipse((cx - 56, M + 52, cx + 56, M + 164), fill=color)
        text(draw, (cx, M + 96), report.grade or "-", 52, WHITE, True, "mm")
        if report.score is not None:
            text(draw, (cx, M + 142), f"{report.score:.1f}", 16, WHITE, True, "mm")

        # 指标
        metrics = [
            ("出场", f"{report.plays:,}"),
            ("胜率", f"{report.win_rate * 100:.2f}%"),
            ("TOP3", f"{report.top3_rate * 100:.2f}%"),
            ("平均伤害", f"{report.avg_damage:,}"),
            ("平均排名", f"#{report.avg_rank:.2f}"),
        ]
        panel_y = M + 240
        shadowed_card(canvas, (M, panel_y, W - M, panel_y + 110))
        for index, (label, value) in enumerate(metrics):
            bx = M + 24 + index * 246
            text(draw, (bx, panel_y + 24), label, 14, MUTED)
            text(draw, (bx, panel_y + 52), value, 30, ORANGE, True)

        # 左：武器流派
        left_box = (M, panel_y + 130, M + 480, panel_y + 570)
        shadowed_card(canvas, left_box)
        text(draw, (left_box[0] + 22, left_box[1] + 20), "武器流派", 22, INK, True)
        for index, weapon in enumerate(report.weapons[:4]):
            wy = left_box[1] + 62 + index * 68
            icon_chip(canvas, blobs.get(weapon.get("icon") or ""), (left_box[0] + 22, wy), 44, 10)
            text(draw, (left_box[0] + 78, wy + 4), weapon["name"], 18, INK, True)
            text(
                draw,
                (left_box[0] + 78, wy + 30),
                f"{weapon['plays']:,} 场 · 胜率 {weapon['win_rate'] * 100:.1f}% · 伤害 {weapon['avg_damage']:,}",
                12,
                MUTED,
            )
            color = _grade_color(weapon["grade"])
            rounded(draw, (left_box[2] - 70, wy + 8, left_box[2] - 22, wy + 40), 8, color)
            text(draw, (left_box[2] - 46, wy + 24), weapon["grade"] or "-", 16, WHITE, True, "mm")

        # 技能加点
        skill_box = (M, panel_y + 590, M + 480, panel_y + 740)
        shadowed_card(canvas, skill_box)
        text(
            draw, (skill_box[0] + 22, skill_box[1] + 18), "技能加点（T 为武器技能）", 18, INK, True
        )
        if report.skill_builds:
            build = report.skill_builds[0]
            text(draw, (skill_box[0] + 22, skill_box[1] + 54), build["priority"], 30, ORANGE, True)
            order = " · ".join(report.skill_builds[0]["order"].split(" > ")[:15])
            text(draw, (skill_box[0] + 22, skill_box[1] + 100), f"顺序 {order}", 12, MUTED)
        else:
            text(draw, (skill_box[0] + 22, skill_box[1] + 60), "暂无加点数据", 16, MUTED)

        # 右：主流出装
        right_box = (M + 500, panel_y + 130, W - M, panel_y + 500)
        shadowed_card(canvas, right_box)
        text(draw, (right_box[0] + 22, right_box[1] + 20), "主流出装", 22, INK, True)
        for index, build in enumerate(report.item_builds[:3]):
            by = right_box[1] + 64 + index * 104
            for slot, url in enumerate(build.get("images") or []):
                icon = _open_image(blobs.get(url))
                ix = right_box[0] + 22 + slot * 58
                if icon is not None:
                    canvas.alpha_composite(_rounded_image(icon, (48, 48), 8), (ix, by))
                else:
                    rounded(draw, (ix, by, ix + 48, by + 48), 8, (234, 236, 240))
            text(
                draw,
                (right_box[0] + 22, by + 54),
                " / ".join(build.get("items") or [])[:52],
                12,
                INK,
            )
            text(
                draw,
                (right_box[0] + 22, by + 74),
                f"{build['plays']:,} 场 · 胜率 {build['win_rate'] * 100:.1f}%",
                11,
                MUTED,
            )

        # 高分玩家
        players_box = (M + 500, panel_y + 520, W - M, panel_y + 740)
        shadowed_card(canvas, players_box)
        text(draw, (players_box[0] + 22, players_box[1] + 18), "高分玩家", 22, INK, True)
        for index, player in enumerate(report.players[:5]):
            py = players_box[1] + 58 + index * 32
            text(draw, (players_box[0] + 22, py), f"{index + 1}. {player['name']}", 14, INK, True)
            text(draw, (players_box[2] - 22, py), f"{player['mmr']:,} MMR", 14, ORANGE, True, "ra")

        text(
            draw,
            (W - M, height - 28),
            f"数据来源 DAK.GG · 统计更新 {report.updated_at or '未知'}",
            13,
            MUTED,
            anchor="ra",
        )
        return self._save(canvas, "character")

    # ------------------------------------------------------------------ 段位统计
    async def tier(self, report: TierReport) -> str:
        urls: list[str | None] = []
        for row in report.rows:
            urls += [row.icon, row.full_icon]
        blobs = await self._blobs(urls)
        return await asyncio.to_thread(self._draw_tier, report, blobs)

    def _draw_tier(self, report: TierReport, blobs: dict[str, bytes]) -> str:
        rows = max(1, math.ceil(len(report.rows) / 5))
        height = 120 + 160 + 20 + rows * 218 + 60
        canvas = Image.new("RGBA", (W, height), BG)
        draw = ImageDraw.Draw(canvas)
        shadowed_card(canvas, (M, M, W - M, M + 100))
        text(draw, (M + 26, M + 20), "段位统计", 34, INK, True)
        meta = f"{report.season_name} · 全体 {report.total:,} 人"
        if report.updated_at:
            meta += f" · 更新 {report.updated_at}"
        text(draw, (M + 26, M + 66), meta, 15, MUTED)

        # 分数线
        cut_y = M + 120
        cutoffs = [
            ("永恒", report.eternal_mmr, 8),
            ("半神", report.demigod_mmr, 7),
        ]
        for index, (label, mmr, tier_id) in enumerate(cutoffs):
            bx = M + index * 630
            shadowed_card(canvas, (bx, cut_y, bx + 610, cut_y + 130))
            icon = _open_image(
                blobs.get(
                    next((row.full_icon for row in report.rows if row.tier_id == tier_id), "")
                )
            )
            if icon is not None:
                canvas.alpha_composite(_rounded_image(icon, (90, 90), 14), (bx + 24, cut_y + 20))
            text(draw, (bx + 136, cut_y + 26), label, 30, INK, True)
            text(
                draw,
                (bx + 136, cut_y + 72),
                f"{mmr:,} RP" if mmr is not None else "暂无数据",
                24,
                ORANGE,
                True,
            )

        # 段位网格
        grid_y = cut_y + 160
        cols = 5
        card_w, card_h, gap = 238, 200, 18
        for index, row in enumerate(report.rows):
            col, line = index % cols, index // cols
            x = M + col * (card_w + gap)
            y = grid_y + line * (card_h + gap)
            shadowed_card(canvas, (x, y, x + card_w, y + card_h))
            icon = _open_image(blobs.get(row.icon or ""))
            if icon is not None:
                canvas.alpha_composite(
                    _rounded_image(icon, (100, 100), 14), (x + (card_w - 100) // 2, y + 14)
                )
            text(draw, (x + card_w / 2, y + 126), row.name, 20, INK, True, "ma")
            text(draw, (x + card_w / 2, y + 154), f"{row.count:,} 人", 16, ORANGE, True, "ma")
            text(
                draw,
                (x + card_w / 2, y + 178),
                f"占比 {row.rate * 100:.2f}%",
                12,
                MUTED,
                anchor="ma",
            )

        text(
            draw,
            (W - M, height - 28),
            f"数据来源 DAK.GG · 生成于 {_now_label()}",
            13,
            MUTED,
            anchor="ra",
        )
        return self._save(canvas, "tier")

    # ------------------------------------------------------------------ 装备
    async def item(self, result: ItemQueryResult) -> str:
        if result.item is None:
            raise ValueError("没有可渲染的装备")
        item = result.item
        urls: list[str | None] = [item.image_url]
        urls += [m.get("image_url") for m in item.materials]
        blobs = await self._blobs(urls)
        return await asyncio.to_thread(self._draw_item, item, blobs)

    def _draw_item(self, item, blobs: dict[str, bytes]) -> str:
        lines = [line for line in item.description if line]
        desc_lines = lines[:8]
        height = 360 + len(desc_lines) * 30 + (120 if item.materials else 40)
        canvas = Image.new("RGBA", (1000, height), BG)
        draw = ImageDraw.Draw(canvas)
        quality = {
            "Common": (150, 156, 168),
            "Uncommon": (76, 175, 80),
            "Rare": (64, 132, 220),
            "Epic": (156, 88, 210),
            "Legend": (236, 150, 60),
            "Mythic": (214, 70, 70),
        }.get(item.grade, (120, 126, 138))
        shadowed_card(canvas, (M, M, 1000 - M, M + 170))
        draw.rounded_rectangle((M, M, M + 170, M + 170), radius=20, fill=quality + (255,))
        icon = _open_image(blobs.get(item.image_url or ""))
        if icon is not None:
            canvas.alpha_composite(_rounded_image(icon, (140, 140), 16), (M + 15, M + 15))
        grade_cn = {
            "Common": "一般",
            "Uncommon": "高级",
            "Rare": "稀有",
            "Epic": "英雄",
            "Legend": "传说",
            "Mythic": "超凡",
        }.get(item.grade, item.grade)
        kind = "武器" if item.type == "Weapon" else "防具"
        text(draw, (M + 200, M + 34), item.name, 40, INK, True)
        text(draw, (M + 200, M + 92), f"{grade_cn} · {kind} · ID {item.id}", 18, quality, True)

        y = M + 200
        text(draw, (M + 10, y), "装备属性与说明", 20, INK, True)
        y += 40
        for line in desc_lines:
            text(draw, (M + 10, y), line, 16, (60, 64, 72))
            y += 30

        if item.materials:
            y += 20
            text(draw, (M + 10, y), "合成材料", 20, INK, True)
            y += 40
            for index, material in enumerate(item.materials[:4]):
                mx = M + 10 + index * 240
                icon = _open_image(blobs.get(material.get("image_url") or ""))
                if icon is not None:
                    canvas.alpha_composite(_rounded_image(icon, (52, 52), 8), (mx, y))
                text(draw, (mx + 64, y + 12), material["name"], 15, INK, True)

        text(
            draw,
            (1000 - M, height - 24),
            f"数据来源 DAK.GG · {_now_label()}",
            12,
            MUTED,
            anchor="ra",
        )
        return self._save(canvas, "item")

    # ------------------------------------------------------------------ 分数线
    async def score(self, report) -> str:
        urls = [
            "https://cdn.dak.gg/assets/er/images/rank/full/8.png",
            "https://cdn.dak.gg/assets/er/images/rank/full/7.png",
        ]
        blobs = await self._blobs(urls)
        return await asyncio.to_thread(self._draw_score, report, blobs)

    def _draw_score(self, report, blobs: dict[str, bytes]) -> str:
        width, height = 1000, 560
        canvas = Image.new("RGBA", (width, height), BG)
        draw = ImageDraw.Draw(canvas)
        header = (M, M, width - M, M + 140)
        rounded(draw, header, 20, DARKER)
        text(draw, (M + 30, M + 30), "段位分数线", 38, WHITE, True)
        text(
            draw,
            (M + 30, M + 92),
            f"{report.season_name} · {server_label(report.server)} · "
            + (
                "刚刚更新"
                if report.updated_minutes in (None, 0)
                else f"{report.updated_minutes} 分钟前更新"
            ),
            16,
            (220, 224, 232),
        )

        cards = [
            ("最高 RP", report.highest, None, (255, 196, 64)),
            ("永恒分数线", report.eternal, "8", (236, 110, 150)),
            ("半神分数线", report.demigod, "7", (214, 170, 90)),
        ]
        for index, (label, value, tier, accent) in enumerate(cards):
            bx = M + index * 320
            box = (bx, M + 170, bx + 300, M + 460)
            shadowed_card(canvas, box)
            draw.rounded_rectangle((bx, M + 170, bx + 300, M + 178), radius=4, fill=accent)
            if tier:
                icon = _open_image(
                    blobs.get(f"https://cdn.dak.gg/assets/er/images/rank/full/{tier}.png")
                )
                if icon is not None:
                    canvas.alpha_composite(_rounded_image(icon, (90, 90), 12), (bx + 105, M + 200))
            text(draw, (bx + 150, M + 320), label, 18, MUTED, anchor="mm")
            text(
                draw,
                (bx + 150, M + 380),
                f"{value:,} RP" if value is not None else "暂无数据",
                30,
                accent,
                True,
                "mm",
            )

        text(
            draw,
            (width - M, height - 28),
            f"数据来源 DAK.GG · 生成于 {_now_label()}",
            13,
            MUTED,
            anchor="ra",
        )
        return self._save(canvas, "score")

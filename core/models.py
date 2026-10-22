"""公共常量与轻量工具函数。"""

from __future__ import annotations

from typing import Any

# 模式名称 -> DAK.GG matchingMode
MATCHING_MODES: dict[str, int] = {
    "排位": 3,
    "rank": 3,
    "ranked": 3,
    "匹配": 2,
    "normal": 2,
    "钴协议": 6,
    "cobalt": 6,
    "全部": 0,
    "all": 0,
}

# 查询接口实际支持的模式（联盟/孤狼经实测上游未做过滤，暂不提供）
QUERYABLE_MODES = {0, 2, 3, 6}

MATCHING_MODE_NAMES: dict[int, str] = {
    0: "全部",
    2: "匹配",
    3: "排位",
    6: "钴协议",
    8: "联盟",
    9: "孤狼",
    11: "其他",
}

# DAK.GG 的 matchingMode 查询参数
MATCHING_MODE_QUERY: dict[int, str] = {
    0: "ALL",
    2: "NORMAL",
    3: "RANK",
    6: "COBALT",
}

TEAM_MODE_NAMES: dict[int, str] = {
    0: "全部",
    1: "单人",
    2: "双人",
    3: "三人",
    4: "钴协议",
}

# 角色数据 / 角色详情使用的段位口径
RANK_TIER_QUERY: dict[str, str] = {
    "永恒": "in1000",
    "无瑕": "mithril_plus",
    "无暇": "mithril_plus",
    "星陨": "meteorite_plus",
    "灭钻": "diamond_plus",
    "修罗": "platinum_plus",
    "黄金": "gold",
    "白银": "silver",
    "白银及以下": "silver",
}

# 玩家当前 tierId -> 角色统计使用的段位口径
TIER_ID_TO_QUERY: dict[int, str] = {
    0: "diamond_plus",
    1: "iron",
    2: "bronze",
    3: "silver",
    4: "gold",
    5: "platinum_plus",
    7: "in1000",
    8: "in1000",
    63: "meteorite_plus",
    66: "mithril_plus",
}

GRADE_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}


def parse_matching_mode(value: str | None, default: int = 3) -> int:
    mode = parse_matching_mode_strict(value, default)
    return default if mode is None else mode


def parse_matching_mode_strict(value: str | None, default: int = 3) -> int | None:
    """解析用户输入的模式；无法识别时返回 None（而不是静默回落到默认值）。"""
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in MATCHING_MODES:
        return MATCHING_MODES[text]
    if text.isdigit():
        mode = int(text)
        if mode in MATCHING_MODE_QUERY:
            return mode
    return None


def parse_rank_tier(value: str | None, default: str = "diamond_plus") -> str:
    if not value:
        return default
    text = str(value).strip()
    return RANK_TIER_QUERY.get(text, default)


def pick_overview(
    profile: dict, season_id: int | None, matching_mode: int, team_mode: int = 0
) -> dict | None:
    """只返回同一赛季、同一模式的概览；找不到就是没数据，不再回落到全部模式。"""
    candidates = [
        item
        for item in (profile.get("playerSeasonOverviews") or [])
        if (season_id is None or as_int(item.get("seasonId")) == as_int(season_id))
        and as_int(item.get("matchingModeId")) == matching_mode
    ]
    if not candidates:
        return None
    for item in candidates:
        if as_int(item.get("teamModeId")) == team_mode:
            return item
    # 同一模式内优先团队口径为“全部”的概览，否则取第一条，标签仍显示所选模式
    return min(candidates, key=lambda item: as_int(item.get("teamModeId")))


def latest_season_id(profile: dict) -> int | None:
    player = profile.get("player") or {}
    last = player.get("lastPlayedSeasonId")
    if last:
        return int(last)
    for season in profile.get("playerSeasons") or []:
        if season.get("seasonId"):
            return int(season["seasonId"])
    overviews = profile.get("playerSeasonOverviews") or []
    if overviews:
        return int(overviews[0].get("seasonId") or 0) or None
    return None


def get_season_rank(profile: dict, season_id: int | None) -> dict:
    """返回玩家在指定赛季的段位信息（mmr / tierId / tierGradeId / tierMmr）。"""
    if season_id is None:
        return {}
    for season in profile.get("playerSeasons") or []:
        if int(season.get("seasonId") or 0) == int(season_id):
            return season
    return {}


def div(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    try:
        d = float(denominator or 0)
        if d == 0:
            return default
        return float(numerator or 0) / d
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def percent(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def fmt_int(value: Any) -> str:
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"


def fmt_signed(value: Any) -> str:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return "0"
    return f"+{number}" if number > 0 else str(number)


def tier_name(tiers: dict, tier_id: int) -> str:
    for tier in tiers.get("tiers") or []:
        if int(tier.get("id") or 0) == int(tier_id or 0):
            return str(tier.get("name") or tier.get("key") or "未知")
    return "未定级"


def tier_image(tiers: dict, tier_id: int) -> str:
    for tier in tiers.get("tiers") or []:
        if int(tier.get("id") or 0) == int(tier_id or 0):
            return _normalize_url(tier.get("iconUrl") or tier.get("imageUrl") or "")
    return ""


def _normalize_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


def character_map(characters: dict) -> dict[int, dict]:
    return {
        int(char.get("id") or 0): char
        for char in (characters.get("characters") or [])
        if char.get("id") is not None
    }


def mastery_map(masteries: dict) -> dict[int, dict]:
    return {
        int(item.get("id") or 0): item
        for item in (masteries.get("masteries") or [])
        if item.get("id") is not None
    }


def item_map(items: dict) -> dict[int, dict]:
    return {
        int(item.get("id") or 0): item
        for item in (items.get("items") or [])
        if item.get("id") is not None
    }


def normalize_character_query(text: str) -> str:
    return str(text or "").strip().lower().replace("·", "").replace(" ", "").replace("_", "")


def find_characters(characters: dict, query: str) -> list[dict]:
    """按名称、英文 key 查找角色，返回按匹配度排序的列表。"""
    q = normalize_character_query(query)
    if not q:
        return []
    exact: list[dict] = []
    prefix: list[dict] = []
    contains: list[dict] = []
    for char in characters.get("characters") or []:
        name = normalize_character_query(char.get("name") or "")
        key = normalize_character_query(char.get("key") or "")
        if q in (name, key):
            exact.append(char)
        elif name.startswith(q) or key.startswith(q):
            prefix.append(char)
        elif q in name or q in key:
            contains.append(char)
    return exact + prefix + contains


def grade_of(score: float | None) -> str:
    if score is None:
        return "-"
    if score >= 90:
        return "S"
    if score >= 78:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"

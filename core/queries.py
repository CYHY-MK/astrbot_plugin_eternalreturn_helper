"""把 DAK.GG 的原始数据整理成便于展示的查询结果。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime

from .api import DakGGClient, UpstreamError
from .models import (
    MATCHING_MODE_NAMES,
    MATCHING_MODE_QUERY,
    TIER_ID_TO_QUERY,
    as_int,
    character_map,
    div,
    find_characters,
    item_map,
    mastery_map,
    parse_matching_mode,
    parse_rank_tier,
    pick_overview,
    tier_image,
    tier_name,
)
from .rating import RatingResult, rate_player

# 段位分布排序：t<10 时 *10，让 63/66 排在 6 与 7 之间
_TIER_ORDER = {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60, 63: 63, 66: 66, 7: 70, 8: 80}

SERVERS: list[tuple[str, str, list[str]]] = [
    ("seoul", "亚1(首尔)", ["亚1", "亚一", "国际服"]),
    ("asia2", "亚2", ["亚2", "亚二", "国服"]),
    ("asia3", "亚3", ["亚3", "亚三"]),
    ("ohio", "美1", ["美1", "美一", "美服"]),
    ("frankfurt", "欧1", ["欧1", "欧一", "欧服"]),
    ("saopaulo", "南1", ["南1", "南一", "南美"]),
    ("global", "全球", []),
]


def convert_server(value: str | None) -> str:
    if not value or not str(value).strip():
        return "seoul"
    text = str(value).strip().lower()
    for key, _label, _aliases in SERVERS:
        if key.lower() == text:
            return key
    for key, _label, aliases in SERVERS:
        if any(alias.lower() == text for alias in aliases):
            return key
    return "seoul"


def server_label(key: str) -> str:
    for server_key, label, _aliases in SERVERS:
        if server_key == key:
            return label
    return key


@dataclass
class RatingView:
    grade: str
    label: str
    score: float | None
    message: str

    @classmethod
    def from_result(cls, result: RatingResult) -> RatingView:
        return cls(result.grade, result.label, result.score, result.message)


@dataclass
class MatchItem:
    game_id: int
    rank: int
    mode: str
    character: str
    level: int
    weapon: str
    kills: int
    assists: int
    deaths: int
    team_kills: int
    damage: int
    mmr_gain: int
    mmr_after: int
    kda: float
    played_at: str
    equipment: list[str] = field(default_factory=list)
    character_image: str = ""
    weapon_icon: str = ""
    item_icons: list[str] = field(default_factory=list)

    @property
    def rank_text(self) -> str:
        return "逃离" if self.rank == 99 else f"#{self.rank}"

    @property
    def kda_text(self) -> str:
        return f"{self.kda:.2f}"

    @property
    def mmr_text(self) -> str:
        if self.mmr_gain > 0:
            return f"+{self.mmr_gain}"
        return str(self.mmr_gain)


@dataclass
class CharacterUse:
    name: str
    plays: int
    win_rate: float
    top3_rate: float
    avg_rank: float
    avg_damage: int
    mmr_gain: int
    image: str = ""


@dataclass
class DuoMate:
    name: str
    plays: int
    win_rate: float
    avg_rank: float
    image: str = ""


@dataclass
class PlayerReport:
    nickname: str
    level: int
    season_name: str
    mode_id: int
    mode_name: str
    tier: str
    mmr: int
    tier_grade: int
    tier_mmr: int
    rank_global: int | None
    rank_local: int | None
    stats: dict
    rating: RatingView | None
    characters: list[CharacterUse]
    duo: list[DuoMate]
    matches: list[MatchItem]
    mmr_trend: list[dict]
    character_image: str = ""
    avatar: str = ""
    banner: str = ""
    tier_icon: str = ""


@dataclass
class CharacterStatRow:
    character: str
    character_id: int
    weapon: str
    grade: str
    score: float | None
    plays: int
    win_rate: float
    top3_rate: float
    avg_damage: int
    avg_rank: float
    pick_rate: float
    rank_text: str
    character_image: str = ""
    weapon_icon: str = ""


@dataclass
class CharacterStatsReport:
    tier_label: str
    mode_name: str
    season_name: str
    total_players: int
    total_games: int
    rows: list[CharacterStatRow]
    updated_at: str | None = None


@dataclass
class TierRow:
    tier_id: int
    name: str
    count: int
    rate: float
    icon: str = ""
    full_icon: str = ""


@dataclass
class TierReport:
    season_name: str
    total: int
    rows: list[TierRow]
    eternal_mmr: int | None
    demigod_mmr: int | None
    server: str
    updated_at: str | None = None


@dataclass
class EternalScoreReport:
    season_name: str
    server: str
    highest: int | None
    eternal: int | None
    demigod: int | None
    updated_minutes: int | None


@dataclass
class ItemView:
    id: int
    name: str
    grade: str
    type: str
    description: list[str]
    materials: list[dict]
    image_url: str


@dataclass
class ItemQueryResult:
    query: str
    matches: list[dict]
    item: ItemView | None
    total: int


@dataclass
class CharacterDetail:
    name: str
    key: str
    archetypes: list[str]
    grade: str
    score: float | None
    plays: int
    win_rate: float
    top3_rate: float
    avg_damage: int
    avg_rank: float
    weapons: list[dict]
    skill_builds: list[dict]
    item_builds: list[dict]
    players: list[dict]
    updated_at: str | None
    image_url: str
    profile_image: str = ""
    banner: str = ""
    weapon_icon: str = ""


class EternalReturnService:
    """查询服务：组合 DAK.GG 接口并计算结果。"""

    def __init__(self, client: DakGGClient, config: dict | None = None) -> None:
        self.client = client
        self.config = config or {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 通用
    def _default_mode(self) -> int:
        return parse_matching_mode(self.config.get("default_matching_mode"), 3)

    def _default_tier(self) -> str:
        return parse_rank_tier(self.config.get("default_rank_tier"), "diamond_plus")

    async def _static(self, *, include_items: bool = False) -> dict:
        tasks = [
            self.client.characters(),
            self.client.masteries(),
            self.client.tiers(),
            self.client.current_season(),
        ]
        if include_items:
            tasks.append(self.client.items())
        results = await asyncio.gather(*tasks)
        characters, masteries, tiers, season = results[:4]
        data = {
            "characters": characters,
            "characters_by_id": character_map(characters),
            "masteries_by_id": mastery_map(masteries),
            "tiers": tiers,
            "season": season,
            "season_name": str(season.get("name") or "当前赛季"),
        }
        if include_items:
            data["items_by_id"] = item_map(results[4])
        return data

    def _fire_sync(self, nickname: str) -> None:
        if not self.config.get("auto_sync", True):
            return
        task = asyncio.create_task(self._sync_safe(nickname))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sync_safe(self, nickname: str) -> None:
        try:
            await self.client.sync_player(nickname)
        except Exception:  # noqa: BLE001 - 同步失败不影响主流程
            pass

    async def close(self) -> None:
        """取消后台同步任务，供插件卸载时调用。"""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------ 玩家
    async def player_report(
        self,
        nickname: str,
        mode_text: str | None = None,
        *,
        recent_limit: int = 6,
    ) -> PlayerReport:
        mode = parse_matching_mode(mode_text, self._default_mode())
        profile = await self.client.profile(nickname)
        self._fire_sync(nickname)

        player = profile.get("player") or {}
        display_name = str(player.get("name") or nickname)
        seasons = [
            season for season in (profile.get("playerSeasons") or []) if season.get("seasonId")
        ]
        if not seasons:
            raise UpstreamError("该玩家无任何赛季有游玩数据。")
        season_info = seasons[0]
        season_id = int(season_info.get("seasonId") or 0)

        static_task = asyncio.create_task(self._static(include_items=True))
        seasons_data = await self.client.seasons()
        season_key = None
        for season in seasons_data.get("seasons") or []:
            if int(season.get("id") or 0) == season_id:
                season_key = season.get("key")
                break

        matching_query = MATCHING_MODE_QUERY.get(mode, "RANK")
        matches_task = asyncio.create_task(
            self.client.matches(
                display_name,
                season_key=season_key,
                matching_mode=matching_query,
                team_mode="ALL",
                page=1,
            )
        )
        character_stats_task = asyncio.create_task(
            self._character_stats_for(mode, season_info.get("tierId"))
        )
        static = await static_task
        matches = (await matches_task).get("matches") or []
        try:
            character_stats = await character_stats_task
        except Exception:  # noqa: BLE001 - 评分缺少基线时退化为“数据不足”
            character_stats = None

        overview = pick_overview(profile, mode, 0)
        stats = self._build_stats(overview or {})
        characters_by_id = static["characters_by_id"]
        masteries = static["masteries_by_id"]
        items_by_id = static.get("items_by_id") or {}

        tier = static["tiers"]
        tier_id = int(season_info.get("tierId") or 0)
        tier_text = tier_name(tier, tier_id) if mode == 3 else "非排位"
        rank_global, rank_local = self._find_rank(profile, tier_id, mode)

        mmr_trend = self._mmr_trend(profile)
        rating = None
        if self.config.get("show_rating", True) and matches:
            result = rate_player(
                matches,
                character_stats,
                mode,
                player_level=as_int(player.get("accountLevel")),
                mmr_history=[point["mmr"] for point in mmr_trend],
            )
            rating = RatingView.from_result(result)

        character_uses = self._character_uses(overview or {}, characters_by_id)
        duo = self._duo_mates(profile, characters_by_id)
        recent = [
            self._match_item(game, characters_by_id, masteries, items_by_id)
            for game in matches[: max(0, int(recent_limit))]
        ]

        character_image = ""
        banner = ""
        avatar = ""
        overview_characters = (overview or {}).get("characterStats") or []
        if overview_characters:
            char = characters_by_id.get(int(overview_characters[0].get("key") or 0))
            if char:
                character_image = _normalize_url(
                    char.get("communityImageUrl") or char.get("imageUrl") or ""
                )
                skins = char.get("skins") or []
                avatar = _normalize_url(
                    (skins[0].get("imageUrl") if skins else "") or char.get("imageUrl") or ""
                )
                banner = avatar or character_image

        return PlayerReport(
            nickname=display_name,
            level=as_int(player.get("accountLevel")),
            season_name=static["season_name"],
            mode_id=mode,
            mode_name=MATCHING_MODE_NAMES.get(mode, "排位"),
            tier=tier_text,
            mmr=as_int(season_info.get("mmr")),
            tier_grade=as_int(season_info.get("tierGradeId")),
            tier_mmr=as_int(season_info.get("tierMmr")),
            rank_global=rank_global,
            rank_local=rank_local,
            stats=stats,
            rating=rating,
            characters=character_uses,
            duo=duo,
            matches=recent,
            mmr_trend=mmr_trend,
            character_image=character_image,
            avatar=avatar,
            banner=banner,
            tier_icon=tier_image(tier, tier_id) if mode == 3 else "",
        )

    async def _character_stats_for(self, mode: int, tier_id) -> dict | None:
        team_mode = "COBALT" if mode == 6 else ("ALL" if mode == 0 else "SQUAD")
        tier_query = TIER_ID_TO_QUERY.get(as_int(tier_id), "diamond_plus")
        return await self.client.character_stats(
            team_mode=team_mode,
            matching_mode=MATCHING_MODE_QUERY.get(mode, "RANK"),
            tier=tier_query,
        )

    def _build_stats(self, overview: dict) -> dict:
        play = as_int(overview.get("play"))
        win = as_int(overview.get("win"))
        top2 = as_int(overview.get("top2"))
        top3 = as_int(overview.get("top3"))
        place = as_int(overview.get("place"))
        kills = as_int(overview.get("playerKill"))
        assists = as_int(overview.get("playerAssistant"))
        deaths = as_int(overview.get("playerDeaths"))
        team_kill = as_int(overview.get("teamKill"))
        damage = as_int(overview.get("damageToPlayer"))
        monster = as_int(overview.get("monsterKill"))
        credit = as_int(overview.get("totalGainVFCredit"))
        vision = as_int(overview.get("viewContribution"))
        mmr_gain = as_int(overview.get("mmrGain"))
        return {
            "play": play,
            "win": win,
            "win_rate": div(win, play),
            "top1_rate": div(win, play),
            "top2_rate": div(top2, play),
            "top3_rate": div(top3, play),
            "avg_rank": div(place, play, 8.0),
            "avg_kills": div(kills, play),
            "avg_assists": div(assists, play),
            "avg_deaths": div(deaths, play),
            "avg_team_kills": div(team_kill, play),
            "avg_damage": int(div(damage, play)),
            "avg_monster": div(monster, play),
            "avg_credit": div(credit, play),
            "avg_vision": div(vision, play),
            "kda": div(kills + assists, max(1, deaths)),
            "mmr_gain": mmr_gain,
        }

    def _find_rank(self, profile: dict, tier_id: int, mode: int) -> tuple[int | None, int | None]:
        if mode != 3 or tier_id not in (7, 8):
            return None, None
        for overview in profile.get("playerSeasonOverviews") or []:
            rank = overview.get("rank")
            if not rank:
                continue
            global_rank = (rank.get("global") or {}).get("rank")
            local_rank = (rank.get("local") or {}).get("rank")
            return (
                as_int(global_rank) or None,
                as_int(local_rank) or None,
            )
        return None, None

    def _mmr_trend(self, profile: dict) -> list[dict]:
        overviews = profile.get("playerSeasonOverviews") or []
        if not overviews:
            return []
        points = (overviews[0].get("mmrStats") or [])[:7]
        trend = []
        for point in reversed(points):
            if not point:
                continue
            raw_date = str(point[0])
            label = f"{raw_date[4:6]}/{raw_date[6:8]}" if len(raw_date) >= 8 else raw_date
            trend.append({"date": label, "mmr": as_int(point[1])})
        return trend

    def _character_uses(self, overview: dict, characters_by_id: dict) -> list[CharacterUse]:
        uses = []
        for stat in (overview.get("characterStats") or [])[:8]:
            char = characters_by_id.get(as_int(stat.get("key"))) or {}
            play = as_int(stat.get("play"))
            uses.append(
                CharacterUse(
                    name=str(char.get("name") or f"角色{stat.get('key')}"),
                    plays=play,
                    win_rate=div(stat.get("win"), play),
                    top3_rate=div(stat.get("top3"), play),
                    avg_rank=div(stat.get("place"), play, 8.0),
                    avg_damage=int(div(stat.get("damageToPlayer"), play)),
                    mmr_gain=as_int(stat.get("mmrGain")),
                    image=_normalize_url(str(char.get("imageUrl") or "")),
                )
            )
        return uses

    def _duo_mates(self, profile: dict, characters_by_id: dict) -> list[DuoMate]:
        for overview in profile.get("playerSeasonOverviews") or []:
            if overview.get("duoStats"):
                mates = []
                for duo in overview["duoStats"][:8]:
                    play = as_int(duo.get("play"))
                    teammate_chars = duo.get("characterStats") or []
                    mate_char = (
                        characters_by_id.get(as_int(teammate_chars[0].get("key")))
                        if teammate_chars
                        else None
                    ) or {}
                    mates.append(
                        DuoMate(
                            name=str(duo.get("nickname") or "未知"),
                            plays=play,
                            win_rate=div(duo.get("win"), play),
                            avg_rank=div(duo.get("place"), play, 8.0),
                            image=_normalize_url(str(mate_char.get("imageUrl") or "")),
                        )
                    )
                return mates
        return []

    def _match_item(
        self,
        game: dict,
        characters_by_id: dict,
        masteries: dict,
        items_by_id: dict | None = None,
    ) -> MatchItem:
        matching_mode = as_int(game.get("matchingMode"))
        character = characters_by_id.get(as_int(game.get("characterNum"))) or {}
        weapon = masteries.get(as_int(game.get("bestWeapon"))) or {}
        items_by_id = items_by_id or {}
        item_icons = []
        for item_id in game.get("equipment") or []:
            item = items_by_id.get(as_int(item_id)) or {}
            icon = _normalize_url(str(item.get("imageUrl") or ""))
            if icon:
                item_icons.append(icon)
        kills = as_int(game.get("playerKill"))
        assists = as_int(game.get("playerAssistant"))
        deaths = as_int(game.get("playerDeaths"))
        kda = float(kills + assists) if deaths == 0 else (kills + assists) / deaths
        rank = 99 if as_int(game.get("escapeState")) != 0 else as_int(game.get("gameRank"))
        return MatchItem(
            game_id=as_int(game.get("gameId")),
            rank=rank,
            mode=MATCHING_MODE_NAMES.get(matching_mode, "对局"),
            character=str(character.get("name") or "未知"),
            level=as_int(game.get("characterLevel")),
            weapon=str(weapon.get("name") or ""),
            kills=kills,
            assists=assists,
            deaths=deaths,
            team_kills=as_int(game.get("teamKill")),
            damage=as_int(game.get("damageToPlayer")),
            mmr_gain=as_int(game.get("mmrGain")),
            mmr_after=as_int(game.get("mmrAfter")),
            kda=kda,
            played_at=_format_match_time(game.get("startDtm")),
            character_image=_normalize_url(str(character.get("imageUrl") or "")),
            weapon_icon=_normalize_url(str(weapon.get("iconUrl") or "")),
            item_icons=item_icons,
        )

    # ------------------------------------------------------------------ 角色数据
    async def character_stats_report(
        self,
        mode_text: str | None = None,
        tier_text: str | None = None,
    ) -> CharacterStatsReport:
        # DAK.GG 的 /character-stats 目前只提供排位（RANK）数据，
        # 其他模式会返回 404（characterStatRow）或 400（matchingMode）。
        mode = 3
        tier_query = parse_rank_tier(tier_text, self._default_tier())
        team_mode = "SQUAD"
        result, static = await asyncio.gather(
            self.client.character_stats(
                team_mode=team_mode,
                matching_mode=MATCHING_MODE_QUERY.get(mode, "RANK"),
                tier=tier_query,
            ),
            self._static(),
        )
        snapshot = result.get("characterStatSnapshot") or {}
        tier_count = as_int(snapshot.get("tierCount"))
        tier_games = as_int(snapshot.get("tierGameCount"))
        characters_by_id = static["characters_by_id"]
        masteries = static["masteries_by_id"]
        rows: list[CharacterStatRow] = []
        for character in snapshot.get("characterStats") or []:
            char = characters_by_id.get(as_int(character.get("key"))) or {}
            for weapon_stat in character.get("weaponStats") or []:
                plays = as_int(weapon_stat.get("count"))
                if plays <= 0:
                    continue
                weapon = masteries.get(as_int(weapon_stat.get("key"))) or {}
                rank = weapon_stat.get("rank") or {}
                rows.append(
                    CharacterStatRow(
                        character=str(char.get("name") or f"角色{character.get('key')}"),
                        character_id=as_int(char.get("id")),
                        weapon=str(weapon.get("name") or "未知武器"),
                        grade=str(weapon_stat.get("tier") or "-"),
                        score=(
                            float(weapon_stat["tierScore"])
                            if weapon_stat.get("tierScore") is not None
                            else None
                        ),
                        plays=plays,
                        win_rate=div(weapon_stat.get("win"), plays),
                        top3_rate=div(weapon_stat.get("top3"), plays),
                        avg_damage=int(div(weapon_stat.get("damageToPlayer"), plays)),
                        avg_rank=div(weapon_stat.get("place"), plays, 8.0),
                        pick_rate=div(plays, tier_games) if tier_games else 0.0,
                        rank_text=(
                            f"#{as_int(rank.get('count'))}/{as_int(rank.get('size'))}"
                            if rank.get("count")
                            else "-"
                        ),
                        character_image=_normalize_url(str(char.get("imageUrl") or "")),
                        weapon_icon=_normalize_url(str(weapon.get("iconUrl") or "")),
                    )
                )
        rows.sort(key=lambda row: (-(row.score or 0), _grade_order(row.grade)))
        updated = result.get("meta", {}).get("updatedAt")
        return CharacterStatsReport(
            tier_label=_tier_label(tier_query),
            mode_name=MATCHING_MODE_NAMES.get(mode, "排位"),
            season_name=static["season_name"],
            total_players=tier_count,
            total_games=tier_games,
            rows=rows,
            updated_at=_format_timestamp(updated),
        )

    # ------------------------------------------------------------------ 段位统计
    async def tier_report(self, server: str | None = None) -> TierReport:
        server_key = convert_server(server)
        current = await self.client.current_season()
        distributions, leaderboard, tiers = await asyncio.gather(
            self.client.tier_distribution(team_mode="SQUAD"),
            self.client.leaderboard(
                season_key=str(current.get("type") or ""),
                server_name=server_key,
                team_mode="SQUAD",
            ),
            self.client.tiers(),
        )
        counts: dict[int, int] = {}
        rates: dict[int, float] = {}
        for item in distributions.get("distributions") or []:
            tier_type = as_int(item.get("tierType"))
            counts[tier_type] = counts.get(tier_type, 0) + as_int(item.get("count"))
            rates[tier_type] = rates.get(tier_type, 0.0) + float(item.get("rate") or 0)
        rows = [
            TierRow(
                tier_id=tier_type,
                name=tier_name(tiers, tier_type),
                count=counts[tier_type],
                rate=rates.get(tier_type, 0.0),
                icon=_normalize_url(
                    next(
                        (
                            str(t.get("iconUrl") or "")
                            for t in tiers.get("tiers") or []
                            if as_int(t.get("id")) == tier_type
                        ),
                        "",
                    )
                ),
                full_icon=_normalize_url(
                    next(
                        (
                            str(t.get("imageUrl") or "")
                            for t in tiers.get("tiers") or []
                            if as_int(t.get("id")) == tier_type
                        ),
                        "",
                    )
                ),
            )
            for tier_type in sorted(counts, key=lambda t: _TIER_ORDER.get(t, t))
        ]
        eternal, demigod = _resolve_cutoffs(leaderboard)
        return TierReport(
            season_name=str(current.get("name") or "当前赛季"),
            total=sum(counts.values()),
            rows=rows,
            eternal_mmr=eternal,
            demigod_mmr=demigod,
            server=server_key,
            updated_at=_format_timestamp(distributions.get("updatedAt")),
        )

    async def eternal_score_report(self, server: str | None = None) -> EternalScoreReport:
        server_key = convert_server(server)
        current = await self.client.current_season()
        leaderboard = await self.client.leaderboard(
            season_key=str(current.get("type") or ""),
            server_name=server_key,
            team_mode="SQUAD",
        )
        eternal, demigod = _resolve_cutoffs(leaderboard)
        boards = leaderboard.get("leaderboards") or []
        highest = as_int(boards[0].get("mmr")) if boards else None
        updated = leaderboard.get("updatedAt")
        minutes = None
        if updated:
            minutes = max(0, int((_now_ts() - as_int(updated) / 1000) // 60))
        return EternalScoreReport(
            season_name=str(current.get("name") or "当前赛季"),
            server=server_key,
            highest=highest,
            eternal=eternal,
            demigod=demigod,
            updated_minutes=minutes,
        )

    # ------------------------------------------------------------------ 装备
    async def item_query(self, keyword: str, *, limit: int = 15) -> ItemQueryResult:
        keyword = (keyword or "").strip()
        if not keyword:
            return ItemQueryResult(query=keyword, matches=[], item=None, total=0)
        items_data = await self.client.items()
        items = items_data.get("items") or []
        equipment = [item for item in items if item.get("type") in ("Weapon", "Armor")]
        matches = _find_equipment(equipment, keyword)
        if len(matches) != 1:
            return ItemQueryResult(
                query=keyword, matches=matches[:limit], item=None, total=len(matches)
            )
        item = matches[0]
        by_id = {as_int(entry.get("id")): entry for entry in items}
        material_ids = (
            item.get("makeMaterials")
            if item.get("makeMaterials") is not None
            else [item.get("makeMaterial1"), item.get("makeMaterial2")]
        )
        materials = []
        for material_id in material_ids or []:
            material_id = as_int(material_id)
            if material_id <= 0:
                continue
            material = by_id.get(material_id) or {}
            materials.append(
                {
                    "id": material_id,
                    "name": str(material.get("name") or f"材料 {material_id}"),
                    "image_url": _normalize_url(str(material.get("imageUrl") or "")),
                }
            )
        return ItemQueryResult(
            query=keyword,
            matches=matches,
            item=ItemView(
                id=as_int(item.get("id")),
                name=str(item.get("name") or ""),
                grade=str(item.get("grade") or ""),
                type=str(item.get("type") or ""),
                description=equipment_description(str(item.get("tooltip") or "")),
                materials=materials,
                image_url=_normalize_url(str(item.get("imageUrl") or "")),
            ),
            total=1,
        )

    # ------------------------------------------------------------------ 角色详情
    async def character_detail(
        self,
        query: str,
        mode_text: str | None = None,
        tier_text: str | None = None,
    ) -> CharacterDetail:
        mode = parse_matching_mode(mode_text, self._default_mode())
        if mode not in (2, 3, 6):
            # 角色详情页只支持排位 / 匹配 / 钴协议三种统计口径
            mode = 3
        tier_query = parse_rank_tier(tier_text, self._default_tier())
        static = await self._static(include_items=True)
        matches = find_characters(static["characters"], query)
        if not matches:
            raise UpstreamError(f"没有找到角色「{query}」，请检查角色名。")
        if len(matches) > 1:
            names = "、".join(str(char.get("name")) for char in matches[:6])
            raise UpstreamError(f"找到多个角色：{names}，请输入更完整的名称。")
        char = matches[0]
        team_mode = "COBALT" if mode == 6 else "SQUAD"
        data = await self.client.character_detail(
            str(char.get("key") or ""),
            team_mode=team_mode,
            matching_mode=MATCHING_MODE_QUERY.get(mode, "RANK"),
            tier=tier_query,
        )
        snapshot = data.get("characterDetailStatSnapshot") or {}
        detail_stat = snapshot.get("characterDetailStat") or {}
        weapons_raw = detail_stat.get("weaponStats") or []
        items_by_id = static.get("items_by_id") or {}
        masteries = static["masteries_by_id"]

        weapons = []
        for weapon_stat in weapons_raw:
            plays = as_int(weapon_stat.get("count"))
            master = masteries.get(as_int(weapon_stat.get("key"))) or {}
            weapons.append(
                {
                    "name": str(master.get("name") or "未知武器"),
                    "plays": plays,
                    "win_rate": div(weapon_stat.get("win"), plays),
                    "top3_rate": div(weapon_stat.get("top3"), plays),
                    "avg_damage": int(div(weapon_stat.get("damageToPlayer"), plays)),
                    "avg_rank": div(weapon_stat.get("place"), plays, 8.0),
                    "grade": str(weapon_stat.get("tier") or "-"),
                    "score": (
                        float(weapon_stat["tierScore"])
                        if weapon_stat.get("tierScore") is not None
                        else None
                    ),
                    "icon": _normalize_url(str(master.get("iconUrl") or "")),
                }
            )
        weapons.sort(key=lambda row: row["plays"], reverse=True)

        total_plays = sum(row["plays"] for row in weapons)
        total_win = sum(as_int(w.get("win")) for w in weapons_raw)
        total_top3 = sum(as_int(w.get("top3")) for w in weapons_raw)
        total_place = sum(as_int(w.get("place")) for w in weapons_raw)
        total_damage = sum(as_int(w.get("damageToPlayer")) for w in weapons_raw)

        main = next((w for w in weapons_raw if as_int(w.get("count")) > 0), {})
        skill_builds = _build_skill_builds(main.get("skillBuildStats") or [])
        item_builds = _build_item_builds(main.get("itemBuildStats") or [], items_by_id)

        players = _build_top_players(data)
        updated = (snapshot.get("meta") or {}).get("updatedAt")
        return CharacterDetail(
            name=str(data.get("character", {}).get("name") or char.get("name") or query),
            key=str(char.get("key") or ""),
            archetypes=_archetype_names(char.get("charArcheTypes") or []),
            grade=str(main.get("tier") or "-"),
            score=(float(main["tierScore"]) if main.get("tierScore") is not None else None),
            plays=total_plays,
            win_rate=div(total_win, total_plays),
            top3_rate=div(total_top3, total_plays),
            avg_damage=int(div(total_damage, total_plays)),
            avg_rank=div(total_place, total_plays, 8.0),
            weapons=weapons,
            skill_builds=skill_builds,
            item_builds=item_builds,
            players=players,
            updated_at=_format_timestamp(updated),
            image_url=_normalize_url(
                str(
                    data.get("character", {}).get("communityImageUrl")
                    or char.get("communityImageUrl")
                    or char.get("imageUrl")
                    or ""
                )
            ),
            profile_image=_normalize_url(str(char.get("imageUrl") or "")),
            banner=_normalize_url(
                str(
                    (char.get("skins") or [{}])[0].get("imageUrl")
                    or char.get("communityImageUrl")
                    or char.get("imageUrl")
                    or ""
                )
            ),
            weapon_icon=_normalize_url(
                str((masteries.get(as_int(main.get("key"))) or {}).get("iconUrl") or "")
            ),
        )


# ---------------------------------------------------------------------- 辅助


def _normalize_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://dak.gg" + url
    return url


def _format_timestamp(value) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _now_ts() -> float:
    import time

    return time.time()


def _format_match_time(value) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return str(value)[:16]


def _grade_order(grade: str) -> int:
    return {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}.get(str(grade).upper(), 9)


def _tier_label(tier_query: str) -> str:
    labels = {
        "in1000": "永恒 / 前1000",
        "mithril_plus": "无瑕+",
        "meteorite_plus": "星陨+",
        "diamond_plus": "灭钻+",
        "platinum_plus": "修罗+",
        "gold": "黄金+",
        "silver": "白银+",
        "bronze": "青铜+",
        "iron": "铁阎+",
    }
    return labels.get(tier_query, tier_query)


def _resolve_cutoffs(leaderboard: dict) -> tuple[int | None, int | None]:
    eternal = demigod = None
    for cutoff in leaderboard.get("cutoffs") or []:
        tier_type = as_int(cutoff.get("tierType"))
        if tier_type == 8:
            eternal = as_int(cutoff.get("mmr"))
        elif tier_type == 7:
            demigod = as_int(cutoff.get("mmr"))
    if eternal is None and demigod is not None:
        eternal = demigod
    if demigod is None and eternal is not None:
        demigod = eternal
    return eternal, demigod


_ARCHETYPES = {
    "Mage": "法师",
    "Marksman": "射手",
    "Assasin": "刺客",
    "Assassin": "刺客",
    "Warrior": "战士",
    "Tanker": "坦克",
    "Supporter": "辅助",
}


def _archetype_names(archetypes: list) -> list[str]:
    names = []
    for archetype in archetypes:
        text = _ARCHETYPES.get(str(archetype))
        if text and text not in names:
            names.append(text)
    return names


def _build_skill_builds(raw: list) -> list[dict]:
    builds = []
    for build in raw[:3]:
        order_stats = build.get("orderStats") or []
        top_order = str(order_stats[0].get("key") or "") if order_stats else ""
        plays = as_int(build.get("count"))
        builds.append(
            {
                "priority": " > ".join(str(build.get("key") or "")),
                "order": " > ".join(top_order),
                "plays": plays,
                "win_rate": div(build.get("win"), plays),
                "order_plays": as_int(order_stats[0].get("count")) if order_stats else 0,
            }
        )
    return builds


def _build_item_builds(raw: list, items_by_id: dict) -> list[dict]:
    builds = []
    for build in raw[:3]:
        plays = as_int(build.get("count"))
        names = []
        images = []
        for item_id in build.get("key") or []:
            item = items_by_id.get(as_int(item_id)) or {}
            names.append(str(item.get("name") or f"装备{item_id}"))
            images.append(_normalize_url(str(item.get("imageUrl") or "")))
        builds.append(
            {
                "items": names,
                "images": images,
                "plays": plays,
                "win_rate": div(build.get("win"), plays),
            }
        )
    return builds


def _build_top_players(data: dict) -> list[dict]:
    players = {as_int(p.get("userNum")): p for p in data.get("players") or []}
    result = []
    for tier in data.get("playerTiers") or []:
        user = players.get(as_int(tier.get("userNum")))
        if not user:
            continue
        result.append(
            {
                "name": str(user.get("name") or ""),
                "mmr": as_int(tier.get("mmr")),
            }
        )
    result.sort(key=lambda row: row["mmr"], reverse=True)
    return result[:5]


# ---------------------------------------------------------------------- 装备搜索与清洗


def _to_pinyin(text: str) -> str:
    try:
        from pypinyin import Style, lazy_pinyin

        return "".join(lazy_pinyin(text, style=Style.NORMAL, errors=lambda x: list(x)))
    except Exception:  # noqa: BLE001 - 未安装 pypinyin 时退化为原文
        return text


def _find_equipment(items: list[dict], keyword: str) -> list[dict]:
    key = keyword.strip()
    if not key:
        return []
    exact = [
        item
        for item in items
        if str(item.get("id")) == key or str(item.get("name") or "").casefold() == key.casefold()
    ]
    if exact:
        return exact
    pinyin = _to_pinyin(key).casefold()
    phonetic = [
        item for item in items if _to_pinyin(str(item.get("name") or "")).casefold() == pinyin
    ]
    if phonetic:
        return phonetic
    return [
        item
        for item in items
        if key.casefold() in str(item.get("name") or "").casefold()
        or (pinyin and pinyin in _to_pinyin(str(item.get("name") or "")).casefold())
    ]


def equipment_description(tooltip: str) -> list[str]:
    text = re.sub(r"(?i)<br\s*/?>", "\n", tooltip)
    text = re.sub(r"<[^>]*>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
    )
    lines = []
    for line in text.splitlines():
        line = line.strip()
        lines.extend([line[i : i + 38] for i in range(0, len(line), 38)] or [""])
    return lines


GRADE_NAMES = {
    "Common": "一般",
    "Uncommon": "高级",
    "Rare": "稀有",
    "Epic": "英雄",
    "Legend": "传说",
    "Mythic": "超凡",
}

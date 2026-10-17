"""玩家近期表现评分。

参考 EternalReturnLoMu 的 PlayerPerformanceRater 思路重新实现：把最近对局与
DAK.GG 同角色公开基线（平均伤害、平均击杀参与、平均排名）对比，
得到 0.35 ~ 1.80 的倍率，再换算成 S/A/B/C 评价与一句评语。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DAMAGE_WEIGHT = 0.55
TAKEDOWN_WEIGHT = 0.25
PLACEMENT_WEIGHT = 0.20
ESCAPE_EFFECTIVE_RANK = 3.0
NORMAL_RATING_MIN_LEVEL = 50
LARGE_MMR_DROP_THRESHOLD = 200

GRADE_LABELS = {
    "BEGINNER": "新手",
    "MMR_DROP": "段位回调",
    "EXCEPTIONAL": "极佳",
    "EXCELLENT": "优秀",
    "GOOD": "良好",
    "AVERAGE": "平均",
    "BELOW_AVERAGE": "低于平均",
    "POOR": "较差",
    "INSUFFICIENT": "数据不足",
}

GRADE_LETTERS = {
    "EXCEPTIONAL": "S",
    "EXCELLENT": "A",
    "GOOD": "B+",
    "AVERAGE": "B",
    "BELOW_AVERAGE": "C",
    "POOR": "D",
    "BEGINNER": "-",
    "MMR_DROP": "-",
    "INSUFFICIENT": "-",
}

MESSAGES = {
    "BEGINNER": [
        "刚上岛就敢来查战绩，勇气可嘉喵！",
        "新手期最重要的是开心喵～",
        "每一局都在熟悉地图喵！",
        "慢慢来，卢米亚岛不会跑喵。",
        "先把基础操作练熟喵！",
        "你的第一场胜利正在路上喵！",
    ],
    "MMR_DROP": [
        "分数有点回落，休息一下再战喵。",
        "别急，手感会回来的喵。",
        "这段位只是暂时借住喵。",
    ],
    "EXCEPTIONAL": [
        "这数据亮得有点刺眼喵！",
        "最近是不是偷偷加练了喵？",
        "统治级表现，请收下我的膝盖喵！",
        "伤害、击倒、名次全都在线喵！",
        "这就是高分玩家的压迫感喵！",
        "状态好到可以开直播了喵！",
    ],
    "EXCELLENT": [
        "打得很稳，继续保持喵！",
        "最近手感不错喵～",
        "这波发挥值得夸一句喵！",
        "节奏抓得很准喵！",
    ],
    "GOOD": [
        "状态在线，稳步上分喵！",
        "不错不错，再来一局喵！",
    ],
    "AVERAGE": [
        "中规中矩，还有提升空间喵。",
        "正常发挥，稳字当头喵。",
        "距离优秀只差一点点喵。",
        "平均水准，继续加油喵。",
    ],
    "BELOW_AVERAGE": [
        "这几局有点可惜喵。",
        "手感似乎还没热起来喵。",
        "伤害和名次都还能再高一点喵。",
        "别灰心，调整一下思路喵。",
        "状态起伏很正常喵。",
        "下一局换个落点试试喵？",
    ],
    "POOR": [
        "这战绩……需要一点特训喵。",
        "今天的运气可能不太站在你这边喵。",
        "先看看高分玩家的路线吧喵。",
        "数据有点扎心，但还能救喵。",
        "建议先打几把匹配找找手感喵。",
        "别慌，谁都有状态低谷喵。",
    ],
    "INSUFFICIENT": [
        "数据还不够，先多打几局喵～",
        "今天想玩什么角色喵？",
        "喝口水，休息一下喵。",
        "等你的下一份战绩喵！",
        "要不要先看看角色数据喵？",
        "随时可以再来查一次喵～",
    ],
}


@dataclass
class RatingResult:
    grade: str
    label: str
    score: float | None
    message: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "label": self.label,
            "score": self.score,
            "message": self.message,
            "details": self.details,
        }


def build_baselines(character_stats: dict | None) -> dict[int, dict]:
    """从 DAK.GG character-stats 构造每个角色的公开基线。"""
    baselines: dict[int, dict] = {}
    if not character_stats:
        return baselines
    snapshot = character_stats.get("characterStatSnapshot") or {}
    for character in snapshot.get("characterStats") or []:
        weapons = [w for w in character.get("weaponStats") or [] if (w.get("count") or 0) > 0]
        games = sum(int(w.get("count") or 0) for w in weapons)
        if games <= 0:
            continue
        total_place = sum(int(w.get("place") or 0) for w in weapons)
        total_damage = sum(int(w.get("damageToPlayer") or 0) for w in weapons)
        total_takedowns = sum(
            int(w.get("playerKill") or 0) + int(w.get("playerAssistant") or 0) for w in weapons
        )
        baselines[int(character.get("key") or 0)] = {
            "average_damage": total_damage / games,
            "average_takedowns": total_takedowns / games,
            "average_rank": (total_place / games) if total_place > 0 else None,
        }
    return baselines


def rate_player(
    recent_matches: list[dict],
    character_stats: dict | None,
    matching_mode: int,
    *,
    player_level: int = NORMAL_RATING_MIN_LEVEL,
    mmr_history: list[int] | None = None,
) -> RatingResult:
    """对最近对局进行表现评分。"""
    games = [
        game
        for game in recent_matches
        if matching_mode == 0 or int(game.get("matchingMode") or 0) == matching_mode
    ]
    if player_level < NORMAL_RATING_MIN_LEVEL:
        return _result("BEGINNER", None, games)

    history = [int(value) for value in (mmr_history or []) if value]
    if len(history) >= 2:
        current_mmr = history[-1]
        peak_mmr = max(history)
        if peak_mmr - current_mmr > LARGE_MMR_DROP_THRESHOLD:
            return _result("MMR_DROP", None, games)

    baselines = build_baselines(character_stats)
    if not baselines:
        return _result("INSUFFICIENT", None, games)

    samples = []
    for game in games:
        character_num = int(game.get("characterNum") or 0)
        baseline = baselines.get(character_num)
        if baseline:
            samples.append((game, baseline))
    if not samples:
        return _result("INSUFFICIENT", None, games)

    components: list[tuple[float, float]] = []

    expected_damage = sum(sample[1]["average_damage"] for sample in samples)
    actual_damage = sum(float(game.get("damageToPlayer") or 0) for game, _ in samples)
    ratio = _weighted_ratio(actual_damage, expected_damage, DAMAGE_WEIGHT)
    if ratio:
        components.append(ratio)

    expected_takedowns = sum(sample[1]["average_takedowns"] for sample in samples)
    actual_takedowns = sum(
        float(int(game.get("playerKill") or 0) + int(game.get("playerAssistant") or 0))
        for game, _ in samples
    )
    ratio = _weighted_ratio(actual_takedowns, expected_takedowns, TAKEDOWN_WEIGHT)
    if ratio:
        components.append(ratio)

    placement_ratios = []
    for game, baseline in samples:
        expected_rank = baseline["average_rank"]
        if not expected_rank:
            continue
        rank = int(game.get("gameRank") or 0)
        if rank == 99:
            actual_rank = ESCAPE_EFFECTIVE_RANK
        elif 1 <= rank <= 8:
            actual_rank = float(rank)
        else:
            continue
        placement_ratios.append(expected_rank / actual_rank)
    if placement_ratios:
        value = sum(placement_ratios) / len(placement_ratios)
        components.append((min(1.8, max(0.35, value)), PLACEMENT_WEIGHT))

    if not components:
        return _result("INSUFFICIENT", None, games)

    score = sum(value * weight for value, weight in components) / sum(
        weight for _, weight in components
    )
    if score >= 1.45:
        tier = "EXCEPTIONAL"
    elif score >= 1.20:
        tier = "EXCELLENT"
    elif score >= 1.05:
        tier = "GOOD"
    elif score >= 0.85:
        tier = "AVERAGE"
    elif score >= 0.65:
        tier = "BELOW_AVERAGE"
    else:
        tier = "POOR"

    result = _result(tier, score, games)
    result.details = {
        "damage_ratio": _component_value(components, DAMAGE_WEIGHT),
        "takedown_ratio": _component_value(components, TAKEDOWN_WEIGHT),
        "placement_ratio": _component_value(components, PLACEMENT_WEIGHT),
        "samples": len(samples),
    }
    return result


def _weighted_ratio(actual: float, expected: float, weight: float) -> tuple[float, float] | None:
    if expected <= 0:
        return None
    return (min(1.8, max(0.35, actual / expected)), weight)


def _component_value(components: list[tuple[float, float]], weight: float) -> float | None:
    for value, component_weight in components:
        if abs(component_weight - weight) < 1e-9:
            return round(value, 3)
    return None


def _result(tier: str, score: float | None, games: list[dict]) -> RatingResult:
    candidates = MESSAGES.get(tier) or MESSAGES["INSUFFICIENT"]
    if tier == "INSUFFICIENT":
        message = candidates[hash_stable(str(len(games))) % len(candidates)]
    else:
        stable_key = 17
        for game in games:
            stable_key = (stable_key * 31 + int(game.get("gameId") or 0)) % (2**31 - 1)
        message = candidates[stable_key % len(candidates)]
    return RatingResult(
        grade=GRADE_LETTERS.get(tier, "-"),
        label=GRADE_LABELS.get(tier, tier),
        score=round(score, 3) if score is not None else None,
        message=message,
    )


def hash_stable(text: str) -> int:
    value = 0
    for ch in text:
        value = (value * 31 + ord(ch)) % (2**31 - 1)
    return value

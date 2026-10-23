"""把查询结果渲染成聊天文本或 Markdown（用于文转图）。"""

from __future__ import annotations

from .models import fmt_int, fmt_signed, percent
from .queries import (
    GRADE_NAMES,
    CharacterDetail,
    CharacterStatsReport,
    CharacterViewReport,
    EternalScoreReport,
    ItemQueryResult,
    MatchHistoryReport,
    MatchItem,
    PlayerReport,
    SkinViewReport,
    TierReport,
    server_label,
)

DIVIDER = "━━━━━━━━━━━━━━"


def _signed(value: int) -> str:
    return fmt_signed(value)


def _season(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return "当前赛季"
    return name if name.startswith("赛季") else f"赛季 {name}"


# ---------------------------------------------------------------------- 玩家


def format_player_text(report: PlayerReport) -> str:
    lines: list[str] = []
    lines.append("【永恒轮回】玩家战绩")
    header = f"{report.nickname} · Lv.{report.level}"
    if report.mode_id == 3:
        header += f" · {report.tier} {fmt_int(report.mmr)} MMR"
    else:
        header += f" · {report.mode_name}"
    lines.append(header)

    sub = f"{_season(report.season_name)} · {report.mode_name}"
    if report.mode_id == 3 and report.tier in ("永恒", "半神"):
        if report.rank_global:
            sub += f" · 第{report.rank_global}名"
        elif report.rank_local:
            sub += f" · 本服第{report.rank_local}名"
    if report.season_note:
        sub += f" · {report.season_note}"
    lines.append(sub)
    lines.append(DIVIDER)

    stats = report.stats
    lines.append(
        f"场次 {fmt_int(stats['play'])}  胜率 {percent(stats['win_rate'])}  "
        f"TOP3 {percent(stats['top3_rate'])}"
    )
    avg_rank = f"#{stats['avg_rank']:.2f}" if stats.get("play") else "-"
    lines.append(
        f"平均排名 {avg_rank}  平均击杀 {stats['avg_kills']:.2f}  "
        f"平均助攻 {stats['avg_assists']:.2f}"
    )
    lines.append(f"平均TK {stats['avg_team_kills']:.2f}  平均伤害 {fmt_int(stats['avg_damage'])}")
    if report.rating:
        score_text = f" {report.rating.score:.2f}x" if report.rating.score is not None else ""
        lines.append(f"表现评分 {report.rating.grade}{score_text} · {report.rating.message}")
    elif report.rating_note:
        lines.append(f"表现评分：{report.rating_note}")
    lines.append(DIVIDER)

    if report.recent_hidden:
        lines.append("最近对局展示已关闭。")
    elif report.matches:
        lines.append(f"最近对局（{len(report.matches)} 场）")
        for match in report.matches:
            lines.append(_match_line(match))
    else:
        lines.append("最近没有对局记录。")

    if report.characters:
        lines.append("")
        lines.append("常用实验体")
        for use in report.characters[:5]:
            lines.append(
                f"· {use.name} {use.plays}场 胜率{percent(use.win_rate)} "
                f"平均#{use.avg_rank:.1f} 伤害{fmt_int(use.avg_damage)} "
                f"RP{_signed(use.mmr_gain)}"
            )

    if report.duo:
        lines.append("")
        lines.append("一起游玩")
        for mate in report.duo[:3]:
            lines.append(
                f"· {mate.name} {mate.plays}场 胜率{percent(mate.win_rate)} "
                f"平均#{mate.avg_rank:.1f}"
            )

    if report.mmr_trend:
        trend = " → ".join(f"{point['date']} {fmt_int(point['mmr'])}" for point in report.mmr_trend)
        lines.append("")
        lines.append(f"MMR 走势：{trend}")

    lines.append("")
    lines.append("数据来源 DAK.GG")
    return "\n".join(lines)


def _match_line(match: MatchItem) -> str:
    weapon = f"·{match.weapon}" if match.weapon else ""
    rp = f" {fmt_int(match.mmr_after)}({_signed(match.mmr_gain)})"
    date = f" {match.played_at}" if match.played_at else ""
    return (
        f"{match.rank_text} {match.mode} {match.character}{weapon} "
        f"{match.kills}/{match.deaths}/{match.assists} "
        f"伤害{fmt_int(match.damage)}{rp}{date}"
    )


def format_player_markdown(report: PlayerReport) -> str:
    lines = [
        "# 永恒轮回 · 玩家战绩",
        f"**{report.nickname}** · Lv.{report.level} · {_season(report.season_name)} · {report.mode_name}",
    ]
    if report.season_note:
        lines.append(f"> {report.season_note}")
    if report.mode_id == 3:
        rank = f" · 第{report.rank_global}名" if report.rank_global else ""
        lines.append(f"### {report.tier} · {fmt_int(report.mmr)} MMR{rank}")
    stats = report.stats
    avg_rank = f"#{stats['avg_rank']:.2f}" if stats.get("play") else "-"
    lines += [
        "",
        f"- 场次 **{fmt_int(stats['play'])}** · 胜率 **{percent(stats['win_rate'])}** · TOP3 **{percent(stats['top3_rate'])}**",
        f"- 平均排名 **{avg_rank}** · 平均击杀 **{stats['avg_kills']:.2f}** · 平均助攻 **{stats['avg_assists']:.2f}**",
        f"- 平均 TK **{stats['avg_team_kills']:.2f}** · 平均伤害 **{fmt_int(stats['avg_damage'])}**",
    ]
    if report.rating:
        score = f"（{report.rating.score:.2f}x）" if report.rating.score is not None else ""
        lines.append(f"- 表现评分 **{report.rating.grade}**{score} · {report.rating.message}")
    elif report.rating_note:
        lines.append(f"- 表现评分：{report.rating_note}")

    if report.recent_hidden:
        lines += ["", "> 最近对局展示已关闭。"]
    elif report.matches:
        lines += [
            "",
            "## 最近对局",
            "| 排名 | 角色 | K/D/A | 伤害 | MMR |",
            "|---|---|---|---|---|",
        ]
        for match in report.matches:
            lines.append(
                f"| {match.rank_text} | {match.character}·{match.weapon} | "
                f"{match.kills}/{match.deaths}/{match.assists} | "
                f"{fmt_int(match.damage)} | {_signed(match.mmr_gain)} |"
            )

    if report.characters:
        lines += [
            "",
            "## 常用实验体",
            "| 角色 | 场次 | 胜率 | 平均排名 | 伤害 |",
            "|---|---|---|---|---|",
        ]
        for use in report.characters[:6]:
            lines.append(
                f"| {use.name} | {use.plays} | {percent(use.win_rate)} | "
                f"#{use.avg_rank:.1f} | {fmt_int(use.avg_damage)} |"
            )

    if report.duo:
        lines += ["", "## 一起游玩", "| 玩家 | 场次 | 胜率 | 平均排名 |", "|---|---|---|---|"]
        for mate in report.duo[:4]:
            lines.append(
                f"| {mate.name} | {mate.plays} | {percent(mate.win_rate)} | #{mate.avg_rank:.1f} |"
            )

    if report.mmr_trend:
        trend = " → ".join(f"{point['date']} {fmt_int(point['mmr'])}" for point in report.mmr_trend)
        lines += ["", f"MMR 走势：{trend}"]

    lines += ["", "> 数据来源 DAK.GG"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- 角色数据


def format_character_stats_text(report: CharacterStatsReport, limit: int = 30) -> str:
    lines = [
        "【永恒轮回】角色数据",
        f"{report.tier_label} · {report.mode_name} · {_season(report.season_name)}",
    ]
    meta = f"统计 {fmt_int(report.total_games)} 场"
    if report.total_players:
        meta += f" / {fmt_int(report.total_players)} 名玩家"
    if report.updated_at:
        meta += f" · 更新 {report.updated_at}"
    lines += [meta, DIVIDER]

    if not report.rows:
        lines.append("暂无统计数据。")
        return "\n".join(lines)

    for row in report.rows[: max(1, limit)]:
        lines.append(
            f"{row.grade:>2} {row.character}·{row.weapon}  "
            f"胜率{percent(row.win_rate, 2)} 出场{fmt_int(row.plays)}  "
            f"伤害{fmt_int(row.avg_damage)}"
        )
    if len(report.rows) > limit:
        lines.append(f"…… 共 {len(report.rows)} 组角色武器，仅显示前 {limit} 组")
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_character_stats_markdown(report: CharacterStatsReport, limit: int = 60) -> str:
    lines = [
        "# 永恒轮回 · 角色数据",
        f"**{report.tier_label}** · {report.mode_name} · {_season(report.season_name)}",
        f"统计 {fmt_int(report.total_games)} 场 · {fmt_int(report.total_players)} 名玩家",
        "",
        "| 评级 | 角色 | 武器 | 胜率 | 出场 | 平均伤害 |",
        "|---|---|---|---|---|---|",
    ]
    for row in report.rows[: max(1, limit)]:
        lines.append(
            f"| {row.grade} | {row.character} | {row.weapon} | "
            f"{percent(row.win_rate, 2)} | {fmt_int(row.plays)} | {fmt_int(row.avg_damage)} |"
        )
    if len(report.rows) > limit:
        lines += ["", f"共 {len(report.rows)} 组，仅显示前 {limit} 组。"]
    lines += ["", "> 数据来源 DAK.GG"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- 角色详情


def format_character_text(report: CharacterDetail) -> str:
    archetypes = " / ".join(report.archetypes) if report.archetypes else "未知定位"
    lines = [
        "【永恒轮回】角色攻略",
        f"{report.name} · {archetypes}",
    ]
    grade = report.grade or "-"
    score = f"（{report.score:.1f}）" if report.score is not None else ""
    lines.append(f"评级 {grade}{score}")
    lines += [DIVIDER]

    lines.append(
        f"出场 {fmt_int(report.plays)} · 胜率 {percent(report.win_rate)} · "
        f"TOP3 {percent(report.top3_rate)}"
    )
    lines.append(f"平均伤害 {fmt_int(report.avg_damage)} · 平均排名 #{report.avg_rank:.2f}")

    if report.weapons:
        lines += ["", "武器流派"]
        for weapon in report.weapons[:4]:
            lines.append(
                f"· {weapon['name']} {fmt_int(weapon['plays'])}场 "
                f"胜率{percent(weapon['win_rate'])} 伤害{fmt_int(weapon['avg_damage'])} "
                f"评级{weapon['grade']}"
            )

    if report.skill_builds:
        lines += ["", "技能加点（T 为武器技能）"]
        for build in report.skill_builds[:2]:
            lines.append(
                f"· {build['priority']}（{fmt_int(build['plays'])}场 "
                f"胜率{percent(build['win_rate'])}）"
            )
            if build["order"]:
                lines.append(f"  顺序 {build['order']}")

    if report.item_builds:
        lines += ["", "主流出装"]
        for build in report.item_builds[:2]:
            lines.append(
                f"· {' / '.join(build['items'])}（{fmt_int(build['plays'])}场 "
                f"胜率{percent(build['win_rate'])}）"
            )

    if report.players:
        lines += ["", "高分玩家"]
        for player in report.players:
            lines.append(f"· {player['name']} {fmt_int(player['mmr'])} MMR")

    if report.updated_at:
        lines += ["", f"统计更新：{report.updated_at}"]
    lines += ["数据来源 DAK.GG"]
    return "\n".join(lines)


def format_character_markdown(report: CharacterDetail) -> str:
    archetypes = " / ".join(report.archetypes) if report.archetypes else "未知定位"
    lines = [
        f"# {report.name} · 角色攻略",
        f"{archetypes} · 评级 **{report.grade or '-'}**"
        + (f"（{report.score:.1f}）" if report.score is not None else ""),
        "",
        f"- 出场 **{fmt_int(report.plays)}** · 胜率 **{percent(report.win_rate)}** · TOP3 **{percent(report.top3_rate)}**",
        f"- 平均伤害 **{fmt_int(report.avg_damage)}** · 平均排名 **#{report.avg_rank:.2f}**",
    ]
    if report.weapons:
        lines += [
            "",
            "## 武器流派",
            "| 武器 | 出场 | 胜率 | 平均伤害 | 评级 |",
            "|---|---|---|---|---|",
        ]
        for weapon in report.weapons[:5]:
            lines.append(
                f"| {weapon['name']} | {fmt_int(weapon['plays'])} | "
                f"{percent(weapon['win_rate'])} | {fmt_int(weapon['avg_damage'])} | {weapon['grade']} |"
            )
    if report.skill_builds:
        lines += ["", "## 技能加点（T 为武器技能）"]
        for build in report.skill_builds[:3]:
            lines.append(
                f"- **{build['priority']}** · {fmt_int(build['plays'])}场 · 胜率 {percent(build['win_rate'])}"
            )
            if build["order"]:
                lines.append(f"  - 顺序：{build['order']}")
    if report.item_builds:
        lines += ["", "## 主流出装"]
        for build in report.item_builds[:3]:
            lines.append(
                f"- {' / '.join(build['items'])}（{fmt_int(build['plays'])}场 · 胜率 {percent(build['win_rate'])}）"
            )
    if report.players:
        lines += ["", "## 高分玩家", "| 玩家 | MMR |", "|---|---|"]
        for player in report.players:
            lines.append(f"| {player['name']} | {fmt_int(player['mmr'])} |")
    lines += ["", f"> 数据来源 DAK.GG · 统计更新 {report.updated_at or '未知'}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- 段位统计


def format_tier_text(report: TierReport) -> str:
    lines = [
        "【永恒轮回】段位统计",
        f"{_season(report.season_name)} · {server_label(report.server)}",
    ]
    if report.updated_at:
        lines.append(f"更新 {report.updated_at}")
    lines += [f"全体玩家 {fmt_int(report.total)} 人", DIVIDER]
    for row in report.rows:
        bar = "█" * max(1, int(round(row.rate * 100 / 2)))
        lines.append(f"{row.name:<6} {fmt_int(row.count):>9} 人 {row.rate * 100:6.2f}% {bar}")
    if report.eternal_mmr is not None or report.demigod_mmr is not None:
        cutoffs = []
        if report.eternal_mmr is not None:
            cutoffs.append(f"永恒 {fmt_int(report.eternal_mmr)} RP")
        if report.demigod_mmr is not None:
            cutoffs.append(f"半神 {fmt_int(report.demigod_mmr)} RP")
        lines.append("分数线：" + " · ".join(cutoffs))
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_tier_markdown(report: TierReport) -> str:
    lines = [
        "# 永恒轮回 · 段位统计",
        f"**{_season(report.season_name)}** · {server_label(report.server)} · 全体 {fmt_int(report.total)} 人",
        "",
        "| 段位 | 人数 | 占比 |",
        "|---|---|---|",
    ]
    for row in report.rows:
        lines.append(f"| {row.name} | {fmt_int(row.count)} | {row.rate * 100:.2f}% |")
    if report.eternal_mmr is not None:
        lines += ["", f"永恒分数线：**{fmt_int(report.eternal_mmr)} RP**"]
    if report.demigod_mmr is not None:
        lines.append(f"半神分数线：**{fmt_int(report.demigod_mmr)} RP**")
    if report.updated_at:
        lines += ["", f"> 更新于 {report.updated_at} · 数据来源 DAK.GG"]
    return "\n".join(lines)


def format_eternal_score_text(report: EternalScoreReport) -> str:
    minutes = report.updated_minutes
    when = "刚刚" if minutes in (None, 0) else f"{minutes} 分钟前"
    lines = [
        "【永恒轮回】段位分数线",
        f"{_season(report.season_name)} · {server_label(report.server)} · {when}更新",
        DIVIDER,
    ]
    if report.highest is not None:
        lines.append(f"最高 {fmt_int(report.highest)} RP")
    if report.eternal is not None:
        lines.append(f"永恒 {fmt_int(report.eternal)} RP")
    if report.demigod is not None:
        lines.append(f"半神 {fmt_int(report.demigod)} RP")
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_eternal_score_markdown(report: EternalScoreReport) -> str:
    minutes = report.updated_minutes
    when = "刚刚" if minutes in (None, 0) else f"{minutes} 分钟前"
    lines = [
        "# 永恒轮回 · 分数线",
        f"**{_season(report.season_name)}** · {server_label(report.server)} · {when}更新",
        "",
    ]
    if report.highest is not None:
        lines.append(f"- 最高：**{fmt_int(report.highest)} RP**")
    if report.eternal is not None:
        lines.append(f"- 永恒：**{fmt_int(report.eternal)} RP**")
    if report.demigod is not None:
        lines.append(f"- 半神：**{fmt_int(report.demigod)} RP**")
    lines += ["", "> 数据来源 DAK.GG"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- 装备


def format_item_text(result: ItemQueryResult) -> str:
    if not result.query:
        return "请指定装备名、拼音或装备 ID，例如 /查询装备 军刀"
    if not result.matches:
        return f"未找到装备：{result.query}，请检查名称或使用装备 ID。"
    if result.item is None:
        lines = ["找到多件装备，请使用完整名称或 ID："]
        for item in result.matches:
            lines.append(f"· {item.get('name')} ({item.get('id')})")
        if result.total > len(result.matches):
            lines.append(f"共 {result.total} 件，请缩小关键词范围。")
        return "\n".join(lines)

    item = result.item
    grade = GRADE_NAMES.get(item.grade, item.grade)
    kind = "武器" if item.type == "Weapon" else "防具"
    lines = [
        f"【装备】{item.name}",
        f"{grade} · {kind} · ID {item.id}",
        DIVIDER,
    ]
    if item.description and any(item.description):
        lines.extend(item.description)
    else:
        lines.append("暂无属性说明")
    lines.append("")
    if item.materials:
        lines.append("合成材料：" + " / ".join(m["name"] for m in item.materials))
    else:
        lines.append("合成材料：无配方（基础装备或特殊来源）")
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_item_markdown(result: ItemQueryResult) -> str:
    if result.item is None:
        return format_item_text(result)
    item = result.item
    grade = GRADE_NAMES.get(item.grade, item.grade)
    kind = "武器" if item.type == "Weapon" else "防具"
    lines = [
        f"# {item.name}",
        f"**{grade}** · {kind} · ID {item.id}",
        "",
        "## 属性与说明",
    ]
    for line in item.description:
        lines.append(line if line else "")
    lines += ["", "## 合成材料"]
    if item.materials:
        for material in item.materials:
            lines.append(f"- {material['name']} ({material['id']})")
    else:
        lines.append("- 无配方（基础装备或特殊来源）")
    lines += ["", "> 数据来源 DAK.GG · 装备资料"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- 帮助


def help_text(prefix: str, *, version: str = "2.2.1") -> str:
    p = prefix or "/"
    return "\n".join(
        [
            f"【永恒轮回小助手】v{version}",
            "数据来源：DAK.GG（er.dakgg.io）",
            DIVIDER,
            "玩家战绩",
            f"  {p}查询玩家 <昵称> [模式] [页面]  （别名：查玩家 / 永恒查询）",
            f"  {p}绑定昵称 <昵称> · {p}解绑 · {p}我的绑定",
            "",
            "角色与数据",
            f"  {p}查询角色 <角色名> [模式]  （别名：查角色）",
            f"  {p}查询实验体 <昵称> [模式]  （别名：实验体）",
            f"  {p}历史对局 <昵称> [页码] [模式]  （别名：对局记录）",
            f"  {p}查询皮肤 <昵称> [模式]  （别名：皮肤统计）",
            f"  {p}角色数据 [段位]  （别名：角色排行）",
            f"  {p}段位统计 [服务器]  （别名：段位分布）",
            f"  {p}永恒多少分 [服务器]  （分数线）",
            f"  {p}查询装备 <名称/拼音/ID>  （别名：装备查询 / 查装备）",
            "",
            "模式：排位 / 匹配 / 钴协议 / 全部，默认排位",
            "页面：实验体 / 历史对局 / 皮肤统计（也可用独立指令）",
            "个人页优先发送 DAK.GG 官网截图；截图失败自动回退卡片",
            "服务器：亚1 / 亚2 / 亚3 / 美1 / 欧1 / 南1",
            "",
            f"示例：{p}查询玩家 RIOORI 排位",
            f"示例：{p}查询角色 杰琪",
            f"示例：{p}查询装备 军刀",
            "",
            f"版本 {version} · 纯 AI 制作，仅供学习交流",
            "如涉及侵权请联系 QQ 邮箱 3033980089@qq.com",
        ]
    )


# ---------------------------------------------------------------------- 个人页：实验体 / 皮肤 / 历史


def _view_intro(report) -> list[str]:
    header = report.header
    sub = f"{header.nickname} · Lv.{header.level} · {_season(header.season_name)} · {header.mode_name}"
    if header.season_note:
        sub += f" · {header.season_note}"
    stats = header.stats
    avg_rank = f"#{stats['avg_rank']:.2f}" if stats.get("play") else "-"
    return [
        sub,
        f"场次 {fmt_int(stats['play'])}  胜率 {percent(stats['win_rate'])}  "
        f"TOP3 {percent(stats['top3_rate'])}  平均排名 {avg_rank}",
    ]


def format_character_view_text(report: CharacterViewReport, limit: int = 50) -> str:
    lines = ["【永恒轮回】实验体统计"] + _view_intro(report)
    lines.append(DIVIDER)
    if not report.rows:
        lines.append("暂无实验体数据。")
    for row in report.rows[:limit]:
        lines.append(
            f"· {row.name} {row.plays}场 胜率{percent(row.win_rate)} "
            f"TOP3 {percent(row.top3_rate)} 平均#{row.avg_rank:.2f} "
            f"伤害{fmt_int(row.avg_damage)} RP{_signed(row.mmr_gain)}"
        )
    if len(report.rows) > limit:
        lines.append(f"…… 共 {len(report.rows)} 个实验体，仅显示前 {limit} 个")
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_character_view_markdown(report: CharacterViewReport, limit: int = 60) -> str:
    lines = [
        "# 永恒轮回 · 实验体统计",
        f"**{report.header.nickname}** · " + " · ".join(_view_intro(report)),
        "",
        "| 实验体 | 场次 | 胜率 | TOP3 | 平均排名 | 平均伤害 | RP |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.rows[:limit]:
        lines.append(
            f"| {row.name} | {row.plays} | {percent(row.win_rate)} | {percent(row.top3_rate)} | "
            f"#{row.avg_rank:.2f} | {fmt_int(row.avg_damage)} | {_signed(row.mmr_gain)} |"
        )
    lines += ["", "> 数据来源 DAK.GG"]
    return "\n".join(lines)


def format_skin_view_text(report: SkinViewReport, limit: int = 40) -> str:
    lines = ["【永恒轮回】皮肤统计"] + _view_intro(report)
    lines.append(DIVIDER)
    for row in report.rows[:limit]:
        lines.append(
            f"· {row.skin}（{row.character}）{row.plays}场 胜率{percent(row.win_rate)} "
            f"平均#{row.avg_rank:.2f} 伤害{fmt_int(row.avg_damage)}"
        )
    if not report.rows:
        lines.append("暂无皮肤数据。")
    if len(report.rows) > limit:
        lines.append(f"…… 共 {len(report.rows)} 款皮肤，仅显示前 {limit} 款")
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_skin_view_markdown(report: SkinViewReport, limit: int = 40) -> str:
    lines = [
        "# 永恒轮回 · 皮肤统计",
        f"**{report.header.nickname}** · " + " · ".join(_view_intro(report)),
        "",
        "| 皮肤 | 实验体 | 场次 | 胜率 | 平均排名 | 平均伤害 |",
        "|---|---|---|---|---|---|",
    ]
    for row in report.rows[:limit]:
        lines.append(
            f"| {row.skin} | {row.character} | {row.plays} | {percent(row.win_rate)} | "
            f"#{row.avg_rank:.2f} | {fmt_int(row.avg_damage)} |"
        )
    lines += ["", "> 数据来源 DAK.GG"]
    return "\n".join(lines)


def format_match_history_text(report: MatchHistoryReport, limit: int = 20) -> str:
    lines = ["【永恒轮回】历史对局"] + _view_intro(report)
    lines.append(f"第 {report.page}/{report.total_pages} 页 · 共 {fmt_int(report.total_count)} 场")
    lines.append(DIVIDER)
    if not report.matches:
        lines.append("该页没有对局记录。")
    for match in report.matches[:limit]:
        lines.append(_match_line(match))
    lines += ["", "数据来源 DAK.GG"]
    return "\n".join(lines)


def format_match_history_markdown(report: MatchHistoryReport) -> str:
    lines = [
        "# 永恒轮回 · 历史对局",
        f"**{report.header.nickname}** · " + " · ".join(_view_intro(report)),
        f"第 {report.page}/{report.total_pages} 页 · 共 {fmt_int(report.total_count)} 场",
        "",
        "| 排名 | 模式 | 时间 | 实验体 | K/D/A | 伤害 | RP |",
        "|---|---|---|---|---|---|---|",
    ]
    for match in report.matches:
        lines.append(
            f"| {match.rank_text} | {match.mode} | {match.played_at} | "
            f"{match.character} | {match.kills}/{match.deaths}/{match.assists} | "
            f"{fmt_int(match.damage)} | {_signed(match.mmr_gain)} |"
        )
    lines += ["", "> 数据来源 DAK.GG"]
    return "\n".join(lines)

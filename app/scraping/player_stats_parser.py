from bs4 import Tag

from app.scraping.dto import PlayerStatDTO
from app.scraping.parsing_utils import attr_int, clean_text, float_or_none, int_or_none, parse_hltv_id_from_href, soup


def calculate_kd_ratio(kills: int | None, deaths: int | None) -> float | None:
    if kills is None or deaths is None or deaths == 0:
        return None
    return round(kills / deaths, 3)


def _first_visible_cell(row: Tag, css_class: str) -> str | None:
    for cell in row.select(f"td.{css_class}"):
        classes = cell.get("class", [])
        if "hidden" not in classes and "eco-adjusted-data" not in classes:
            return clean_text(cell.get_text(" "))
    return None


def _parse_kd(value: str | None) -> tuple[int | None, int | None]:
    if not value or "-" not in value:
        return None, None
    left, right = value.split("-", 1)
    return int_or_none(left), int_or_none(right)


def parse_player_stats(html: str, mapstats_to_number: dict[int, int] | None = None) -> list[PlayerStatDTO]:
    doc = soup(html)
    stats: list[PlayerStatDTO] = []
    for row in doc.select("[data-player-stat]"):
        link = row.select_one("a[href*='/player/']")
        player_id = attr_int(row, "data-player-id") or parse_hltv_id_from_href(link.get("href") if link else None, "player")
        nickname = clean_text(row.get("data-nickname") or (link.get_text(" ") if link else ""))
        if not player_id or not nickname:
            continue
        kills = attr_int(row, "data-kills")
        deaths = attr_int(row, "data-deaths")
        assists = attr_int(row, "data-assists")
        kd_ratio = float_or_none(row.get("data-kd-ratio")) or calculate_kd_ratio(kills, deaths)
        stats.append(
            PlayerStatDTO(
                map_number=attr_int(row, "data-map-number") or 1,
                hltv_player_id=player_id,
                nickname=nickname,
                hltv_team_id=attr_int(row, "data-team-id"),
                kills=kills,
                deaths=deaths,
                assists=assists,
                kd_diff=int_or_none(row.get("data-kd-diff")),
                kd_ratio=kd_ratio,
                adr=float_or_none(row.get("data-adr")),
                kast=float_or_none(row.get("data-kast")),
                rating=float_or_none(row.get("data-rating")),
                headshot_percentage=float_or_none(row.get("data-hs")),
            )
        )
    mapstats_to_number = mapstats_to_number or {}
    for content in doc.select(".stats-content[id$='-content']"):
        content_id = content.get("id", "")
        mapstats_id = int_or_none(content_id)
        if not mapstats_id or mapstats_id not in mapstats_to_number:
            continue
        map_number = mapstats_to_number[mapstats_id]
        for table in content.select("table.totalstats"):
            team_link = table.select_one("tr.header-row a.teamName[href*='/team/']")
            team_id = parse_hltv_id_from_href(team_link.get("href") if team_link else None, "team")
            for row in table.select("tr"):
                if "header-row" in row.get("class", []):
                    continue
                player_link = row.select_one("td.players a[href*='/player/']")
                player_id = parse_hltv_id_from_href(player_link.get("href") if player_link else None, "player")
                nick_tag = row.select_one(".player-nick") or row.select_one(".smartphone-only.statsPlayerName")
                nickname = clean_text(nick_tag.get_text(" ") if nick_tag else player_link.get_text(" ") if player_link else "")
                if not player_id or not nickname:
                    continue
                kills, deaths = _parse_kd(_first_visible_cell(row, "kd"))
                stats.append(
                    PlayerStatDTO(
                        map_number=map_number,
                        hltv_player_id=player_id,
                        nickname=nickname,
                        hltv_team_id=team_id,
                        kills=kills,
                        deaths=deaths,
                        kd_diff=(kills - deaths) if kills is not None and deaths is not None else None,
                        kd_ratio=calculate_kd_ratio(kills, deaths),
                        adr=float_or_none(_first_visible_cell(row, "adr")),
                        kast=float_or_none(_first_visible_cell(row, "kast")),
                        rating=float_or_none(_first_visible_cell(row, "rating")),
                    )
                )
    return stats

import re
from datetime import UTC, datetime

from app.scraping.dto import RankingSnapshotDTO, RankingTeamDTO
from app.scraping.parsing_utils import attr_int, clean_text, int_or_none, parse_hltv_id_from_href, soup


def parse_ranking(html: str, source_url: str) -> RankingSnapshotDTO:
    doc = soup(html)
    date_value = doc.select_one("[data-ranking-date]")
    ranking_date = datetime.now(UTC).replace(tzinfo=None)
    if date_value and date_value.get("data-ranking-date"):
        parsed = datetime.fromisoformat(date_value["data-ranking-date"])
        ranking_date = parsed
    else:
        header = doc.select_one(".regional-ranking-header-text")
        match = re.search(r"on ([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)?, (\d{4})", header.get_text(" ") if header else "")
        if match:
            ranking_date = datetime.strptime(" ".join(match.groups()), "%B %d %Y")

    rows = doc.select("[data-ranking-team], .ranked-team")
    teams: list[RankingTeamDTO] = []
    previous_rank = 0
    for index, row in enumerate(rows, start=1):
        rank = attr_int(row, "data-rank") or int_or_none(clean_text(row.select_one(".position").get_text(" ") if row.select_one(".position") else None)) or index
        if teams and rank <= previous_rank:
            break
        link = row.select_one("a[href*='/team/']")
        hltv_team_id = attr_int(row, "data-team-id") or parse_hltv_id_from_href(link.get("href") if link else None, "team")
        name_tag = row.select_one("[data-team-name], .name, .teamLineup, a[href*='/team/']")
        name = clean_text(name_tag.get("data-team-name") if name_tag and name_tag.has_attr("data-team-name") else name_tag.get_text(" ") if name_tag else "")
        if not hltv_team_id or not name:
            continue
        country_tag = row.select_one("[data-country], .country")
        country = clean_text(country_tag.get("data-country") if country_tag and country_tag.has_attr("data-country") else country_tag.get_text(" ") if country_tag else "") or None
        points_tag = row.select_one("[data-points], .points")
        points = attr_int(points_tag, "data-points") if points_tag else None
        points = points if points is not None else int_or_none(points_tag.get_text(" ") if points_tag else None)
        logo = row.select_one("img")
        teams.append(RankingTeamDTO(rank=rank, hltv_team_id=hltv_team_id, name=name, country=country, points=points, logo_url=logo.get("src") if logo else None))
        previous_rank = rank
        if len(teams) >= 30:
            break
    return RankingSnapshotDTO(ranking_date=ranking_date, source_url=source_url, teams=teams)

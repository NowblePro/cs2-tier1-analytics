from app.scraping.dto import MapDTO
from app.scraping.parsing_utils import attr_int, clean_text, int_or_none, parse_hltv_id_from_href, soup


def parse_maps(html: str, team1_hltv_id: int | None = None, team2_hltv_id: int | None = None) -> list[MapDTO]:
    doc = soup(html)
    maps: list[MapDTO] = []
    rows = doc.select("[data-match-map], .mapholder")
    for index, row in enumerate(rows, start=1):
        map_number = attr_int(row, "data-map-number") or index
        name_tag = row.select_one("[data-map-name], .mapname")
        name = clean_text(row.get("data-map-name") or (name_tag.get("data-map-name") if name_tag and name_tag.has_attr("data-map-name") else name_tag.get_text(" ") if name_tag else ""))
        if not name:
            continue
        score = row.get("data-score")
        score_team1 = attr_int(row, "data-score-team1")
        score_team2 = attr_int(row, "data-score-team2")
        if score and "-" in score and score_team1 is None and score_team2 is None:
            left, right = score.split("-", 1)
            score_team1 = int_or_none(left)
            score_team2 = int_or_none(right)
        if score_team1 is None and score_team2 is None:
            scores = [int_or_none(item.get_text(" ")) for item in row.select(".results-team-score")]
            if len(scores) >= 2:
                score_team1, score_team2 = scores[0], scores[1]
        if score_team1 is None or score_team2 is None:
            continue
        stats_link = row.select_one("a[href*='/stats/matches/mapstatsid/']")
        mapstats_id = attr_int(row, "data-mapstats-id") or parse_hltv_id_from_href(stats_link.get("href") if stats_link else None, "stats/matches/mapstatsid")
        half_scores = [int_or_none(item.get_text(" ")) for item in row.select(".results-center-half-score span")]
        half_scores = [item for item in half_scores if item is not None]
        winner_hltv_team_id = attr_int(row, "data-winner-team-id")
        if winner_hltv_team_id is None and team1_hltv_id and team2_hltv_id:
            if score_team1 > score_team2:
                winner_hltv_team_id = team1_hltv_id
            elif score_team2 > score_team1:
                winner_hltv_team_id = team2_hltv_id
        maps.append(
            MapDTO(
                map_number=map_number,
                name=name,
                hltv_mapstats_id=mapstats_id,
                score_team1=score_team1,
                score_team2=score_team2,
                winner_hltv_team_id=winner_hltv_team_id,
                first_half_team1=attr_int(row, "data-first-half-team1") if attr_int(row, "data-first-half-team1") is not None else (half_scores[0] if len(half_scores) >= 4 else None),
                first_half_team2=attr_int(row, "data-first-half-team2") if attr_int(row, "data-first-half-team2") is not None else (half_scores[1] if len(half_scores) >= 4 else None),
                second_half_team1=attr_int(row, "data-second-half-team1") if attr_int(row, "data-second-half-team1") is not None else (half_scores[2] if len(half_scores) >= 4 else None),
                second_half_team2=attr_int(row, "data-second-half-team2") if attr_int(row, "data-second-half-team2") is not None else (half_scores[3] if len(half_scores) >= 4 else None),
                overtime=(row.get("data-overtime") or "").lower() == "true",
            )
        )
    return maps

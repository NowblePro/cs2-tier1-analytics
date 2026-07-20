from app.scraping.dto import MatchDTO, TeamDTO
from app.scraping.map_parser import parse_maps
from app.scraping.parsing_utils import attr_int, clean_text, int_or_none, parse_datetime, parse_hltv_id_from_href, soup
from app.scraping.player_stats_parser import parse_player_stats
from app.scraping.round_parser import parse_rounds


def parse_match(html: str, source_url: str, match_id: int | None = None) -> MatchDTO:
    doc = soup(html)
    root = doc.select_one("[data-match-id]") or doc
    canonical = doc.select_one("link[rel='canonical']") or doc.select_one("meta[property='og:url']")
    canonical_url = canonical.get("href") or canonical.get("content") if canonical else None
    source_url = canonical_url or source_url
    hltv_match_id = match_id or attr_int(root, "data-match-id") or parse_hltv_id_from_href(source_url, "matches")
    if not hltv_match_id:
        raise ValueError("Cannot determine HLTV match ID")

    def parse_team(slot: int) -> TeamDTO | None:
        node = doc.select_one(f"[data-team-slot='{slot}']")
        if not node:
            node = doc.select_one(f".team{slot}-gradient")
        if not node:
            return None
        link = node.select_one("a[href*='/team/']")
        team_id = attr_int(node, "data-team-id") or parse_hltv_id_from_href(link.get("href") if link else None, "team")
        name_tag = node.select_one(".teamName")
        name = clean_text(node.get("data-team-name") or (name_tag.get_text(" ") if name_tag else node.get_text(" ")))
        return TeamDTO(hltv_team_id=team_id, name=name) if team_id and name else None

    event = doc.select_one("[data-event-name], .event")
    time_node = doc.select_one(".timeAndEvent .time") or doc.select_one(".time")
    countdown = clean_text(doc.select_one(".countdown").get_text(" ") if doc.select_one(".countdown") else "")
    team1 = parse_team(1)
    team2 = parse_team(2)
    score_team1 = attr_int(root, "data-score-team1")
    score_team2 = attr_int(root, "data-score-team2")
    if score_team1 is None:
        score_team1 = int_or_none(doc.select_one(".team1-gradient .won, .team1-gradient .lost").get_text(" ") if doc.select_one(".team1-gradient .won, .team1-gradient .lost") else None)
    if score_team2 is None:
        score_team2 = int_or_none(doc.select_one(".team2-gradient .won, .team2-gradient .lost").get_text(" ") if doc.select_one(".team2-gradient .won, .team2-gradient .lost") else None)
    winner_hltv_team_id = attr_int(root, "data-winner-team-id")
    if winner_hltv_team_id is None:
        if doc.select_one(".team1-gradient .won") and team1:
            winner_hltv_team_id = team1.hltv_team_id
        elif doc.select_one(".team2-gradient .won") and team2:
            winner_hltv_team_id = team2.hltv_team_id
    status = clean_text(root.get("data-status")) or ("completed" if countdown.lower() == "match over" else "scheduled")
    info_text = clean_text(doc.select_one(".preformatted-text").get_text(" ") if doc.select_one(".preformatted-text") else "")
    best_of = attr_int(root, "data-best-of") or int_or_none(info_text if "Best of" in info_text else None)
    maps = parse_maps(html, team1.hltv_team_id if team1 else None, team2.hltv_team_id if team2 else None)
    mapstats_to_number = {m.hltv_mapstats_id: m.map_number for m in maps if m.hltv_mapstats_id}
    return MatchDTO(
        hltv_match_id=hltv_match_id,
        source_url=source_url,
        match_time=parse_datetime(root.get("data-match-time") or (time_node.get("data-unix") if time_node else None)),
        status=status,
        team1=team1,
        team2=team2,
        winner_hltv_team_id=winner_hltv_team_id,
        event_name=clean_text(event.get("data-event-name") if event and event.has_attr("data-event-name") else event.get_text(" ") if event else "") or None,
        best_of=best_of,
        score_team1=score_team1,
        score_team2=score_team2,
        maps=maps,
        rounds=parse_rounds(html),
        player_stats=parse_player_stats(html, mapstats_to_number),
    )

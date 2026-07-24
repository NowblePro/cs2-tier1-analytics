from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dust2.parser import Dust2Match, parse_dust2_match
from app.grid.ids import stable_negative_id
from app.models.schema import ExternalEntityMap, Match, MatchMap, Player, PlayerMapStat, Round, Team
from app.repositories.team_aliases import find_team_by_alias
from app.scraping.player_stats_parser import calculate_kd_ratio


@dataclass(frozen=True)
class Dust2ImportResult:
    match_id: int
    maps_imported: int
    rounds_imported: int
    player_stats_imported: int
    url: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "match_id": self.match_id,
            "maps_imported": self.maps_imported,
            "rounds_imported": self.rounds_imported,
            "player_stats_imported": self.player_stats_imported,
            "url": self.url,
        }


def import_dust2_match(
    session: Session,
    html: str,
    *,
    match_id: int,
    url: str | None = None,
    import_player_stats: bool = False,
) -> Dust2ImportResult:
    parsed = parse_dust2_match(html)
    match = session.get(Match, match_id)
    if match is None:
        raise ValueError(f"Local match {match_id} was not found")
    team1 = session.get(Team, match.team1_id) if match.team1_id else None
    team2 = session.get(Team, match.team2_id) if match.team2_id else None
    dust2_teams = _resolve_dust2_teams(session, parsed, team1, team2)

    maps_imported = 0
    rounds_imported = 0
    player_stats_imported = 0
    for dust2_map in parsed.maps:
        match_map = session.scalar(select(MatchMap).where(MatchMap.match_id == match.id, MatchMap.map_number == dust2_map.map_number))
        if match_map is None:
            match_map = MatchMap(match_id=match.id, map_number=dust2_map.map_number, name=dust2_map.name)
            session.add(match_map)
            session.flush()
        match_map.name = dust2_map.name
        match_map.score_team1 = dust2_map.score_team1
        match_map.score_team2 = dust2_map.score_team2
        winner = dust2_teams.get(dust2_map.winner_team_name or "")
        match_map.winner_team_id = winner.id if winner else match_map.winner_team_id
        match_map.first_half_team1, match_map.first_half_team2 = _score_after_round(parsed, dust2_map.map_number, 12)
        match_map.second_half_team1 = _second_half_score(dust2_map.score_team1, match_map.first_half_team1)
        match_map.second_half_team2 = _second_half_score(dust2_map.score_team2, match_map.first_half_team2)
        match_map.overtime = (dust2_map.score_team1 + dust2_map.score_team2) > 24
        session.query(Round).filter(Round.match_map_id == match_map.id).delete()
        for dust2_round in [item for item in parsed.rounds if item.map_number == dust2_map.map_number]:
            winner = dust2_teams.get(dust2_round.winner_team_name)
            session.add(
                Round(
                    match_map_id=match_map.id,
                    round_number=dust2_round.round_number,
                    half_number=_half_number(dust2_round.round_number),
                    is_overtime=dust2_round.round_number > 24,
                    winner_team_id=winner.id if winner else None,
                    winner_side=dust2_round.winner_side,
                    end_method=dust2_round.end_method,
                    score_team1_after=dust2_round.score_team1_after,
                    score_team2_after=dust2_round.score_team2_after,
                    is_pistol=dust2_round.is_pistol,
                )
            )
            rounds_imported += 1
        if import_player_stats:
            for stat in [item for item in parsed.player_stats if item.map_number == dust2_map.map_number]:
                team = dust2_teams.get(stat.team_name)
                player = _upsert_player(session, stat.nickname, team, stat.real_name)
                row = session.scalar(select(PlayerMapStat).where(PlayerMapStat.match_map_id == match_map.id, PlayerMapStat.player_id == player.id))
                if row is None:
                    row = PlayerMapStat(match_map_id=match_map.id, player_id=player.id)
                    session.add(row)
                row.team_id = team.id if team else row.team_id
                row.kills = stat.kills
                row.deaths = stat.deaths
                row.kd_diff = stat.kd_diff if stat.kd_diff is not None else (stat.kills - stat.deaths if stat.kills is not None and stat.deaths is not None else None)
                row.kd_ratio = calculate_kd_ratio(stat.kills, stat.deaths)
                row.adr = stat.adr
                row.kast = stat.kast
                row.rating = stat.rating
                player_stats_imported += 1
        maps_imported += 1
    if url:
        _save_match_source(session, url, match.id, parsed)
    return Dust2ImportResult(match_id=match.id, maps_imported=maps_imported, rounds_imported=rounds_imported, player_stats_imported=player_stats_imported, url=url)


def _resolve_dust2_teams(session: Session, parsed: Dust2Match, team1: Team | None, team2: Team | None) -> dict[str, Team]:
    resolved: dict[str, Team] = {}
    for name in [parsed.team1_name, parsed.team2_name]:
        if not name:
            continue
        local = None
        if team1 and _same_team(name, team1.name):
            local = team1
        elif team2 and _same_team(name, team2.name):
            local = team2
        else:
            local = find_team_by_alias(session, name)
        if local:
            resolved[name] = local
    return resolved


def _same_team(left: str, right: str) -> bool:
    return find_key(left) == find_key(right)


def find_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum()).removeprefix("team")


def _score_after_round(parsed: Dust2Match, map_number: int, round_number: int) -> tuple[int | None, int | None]:
    candidates = [item for item in parsed.rounds if item.map_number == map_number and item.round_number <= round_number]
    if not candidates:
        return None, None
    latest = candidates[-1]
    return latest.score_team1_after, latest.score_team2_after


def _second_half_score(total: int | None, first_half: int | None) -> int | None:
    if total is None or first_half is None:
        return None
    return max(0, total - first_half)


def _half_number(round_number: int) -> int:
    if round_number <= 12:
        return 1
    if round_number <= 24:
        return 2
    return 3 + ((round_number - 25) // 6)


def _save_match_source(session: Session, url: str, match_id: int, parsed: Dust2Match) -> None:
    external_id = _dust2_external_id(url)
    row = _external_entity_map(session, "match", external_id)
    if row is None:
        row = ExternalEntityMap(
            provider="dust2",
            entity_type="match",
            external_id=external_id,
            local_table="matches",
            local_id=match_id,
            name=parsed.title,
        )
        session.add(row)
    row.local_id = match_id
    row.name = parsed.title or row.name


def _dust2_external_id(url: str) -> str:
    parts = [part for part in url.split("/") if part]
    if "matches" in parts:
        index = parts.index("matches")
        if index + 1 < len(parts):
            return parts[index + 1]
    return str(stable_negative_id(f"dust2-match:{url}"))


def _upsert_player(session: Session, nickname: str, team: Team | None, real_name: str | None) -> Player:
    hltv_player_id = stable_negative_id(f"dust2-player:{find_key(team.name) if team else 'unknown'}:{find_key(nickname)}")
    player = session.scalar(select(Player).where(Player.hltv_player_id == hltv_player_id))
    if player is None:
        player = Player(hltv_player_id=hltv_player_id, nickname=nickname)
        session.add(player)
        session.flush()
    player.nickname = nickname
    player.real_name = real_name or player.real_name
    player.current_team_id = team.id if team else player.current_team_id
    _save_player_source(session, player, team, nickname)
    return player


def _save_player_source(session: Session, player: Player, team: Team | None, nickname: str) -> None:
    external_id = f"{find_key(team.name) if team else 'unknown'}:{find_key(nickname)}"
    row = _external_entity_map(session, "player", external_id)
    if row is None:
        row = ExternalEntityMap(
            provider="dust2",
            entity_type="player",
            external_id=external_id,
            local_table="players",
            local_id=player.id,
            name=nickname,
        )
        session.add(row)
    row.local_id = player.id
    row.name = nickname


def _external_entity_map(session: Session, entity_type: str, external_id: str) -> ExternalEntityMap | None:
    pending = next(
        (
            row for row in session.new
            if isinstance(row, ExternalEntityMap)
            and row.provider == "dust2"
            and row.entity_type == entity_type
            and row.external_id == external_id
        ),
        None,
    )
    if pending:
        return pending
    return session.scalar(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == "dust2",
            ExternalEntityMap.entity_type == entity_type,
            ExternalEntityMap.external_id == external_id,
        )
    )

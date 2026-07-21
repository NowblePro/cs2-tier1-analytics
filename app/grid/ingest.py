from __future__ import annotations

import hashlib
import json
import re
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.grid.client import GridApiError, GridClient, GridSeriesSummary
from app.grid.ids import stable_negative_id
from app.repositories import AnalyticsRepository
from app.repositories.team_aliases import find_team_by_alias
from app.scraping.dto import MapDTO, MatchDTO, PlayerStatDTO, TeamDTO
from app.scraping.player_stats_parser import calculate_kd_ratio
from app.models.schema import Event, GridEntityMap, GridRawSeriesState, Match, Player, RankingSnapshot, RankingSnapshotTeam, Team

logger = logging.getLogger(__name__)

MAP_NAME_ALIASES = {
    "de_dust2": "Dust2",
    "dust2": "Dust2",
    "dust ii": "Dust2",
    "de_mirage": "Mirage",
    "de_inferno": "Inferno",
    "de_nuke": "Nuke",
    "de_ancient": "Ancient",
    "de_anubis": "Anubis",
    "de_vertigo": "Vertigo",
    "de_overpass": "Overpass",
    "de_train": "Train",
    "de_cache": "Cache",
    "de_cbble": "Cobblestone",
    "de_cobblestone": "Cobblestone",
}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalize_map_name(value: str | None) -> str:
    if not value:
        return "GRID Unknown"
    stripped = value.strip()
    if not stripped:
        return "GRID Unknown"
    lowered = stripped.lower().replace("-", "_").replace(" ", "_")
    if lowered in MAP_NAME_ALIASES:
        return MAP_NAME_ALIASES[lowered]
    compact = stripped.replace(" ", "")
    for canonical in ["Ancient", "Anubis", "Cache", "Cobblestone", "Dust2", "Inferno", "Mirage", "Nuke", "Overpass", "Train", "Vertigo"]:
        if compact.lower() == canonical.lower():
            return canonical
    return stripped


def latest_top_team_names(session: Session, limit: int = 30) -> set[str]:
    snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.ranking_date), desc(RankingSnapshot.id)).limit(1))
    if snapshot is None:
        return set()
    rows = session.execute(
        select(Team.name)
        .join(RankingSnapshotTeam, RankingSnapshotTeam.team_id == Team.id)
        .where(RankingSnapshotTeam.snapshot_id == snapshot.id)
        .order_by(RankingSnapshotTeam.rank)
        .limit(limit)
    )
    return {normalize_name(row[0]) for row in rows}


def _team_from_grid(team_node: dict[str, Any], session: Session) -> TeamDTO | None:
    base = team_node.get("baseInfo") or team_node.get("team") or team_node
    grid_id = base.get("id")
    name = base.get("name")
    if not grid_id or not name:
        return None
    existing = find_team_by_alias(session, name)
    return TeamDTO(
        hltv_team_id=existing.hltv_team_id if existing else stable_negative_id(f"grid-team:{grid_id}"),
        name=existing.name if existing else name,
    )


def _team_dto_for_state(team_state: dict[str, Any], session: Session) -> TeamDTO | None:
    return _team_from_grid(team_state, session)


def _round_count(game: dict[str, Any]) -> int | None:
    teams = game.get("teams") or []
    if len(teams) < 2:
        return None
    score1 = teams[0].get("score")
    score2 = teams[1].get("score")
    if score1 is None or score2 is None:
        return None
    return int(score1) + int(score2)


def _player_identity(player_state: dict[str, Any]) -> tuple[str | None, str | None]:
    player = player_state.get("player") or player_state
    return player.get("id"), player.get("name")


def _headshot_percentage(kills: int | None, headshots: int | None) -> float | None:
    if kills in (None, 0) or headshots is None:
        return None
    return round((headshots / kills) * 100, 2)


def _adr(damage_dealt: int | None, rounds: int | None) -> float | None:
    if damage_dealt is None or not rounds:
        return None
    return round(damage_dealt / rounds, 2)


def _series_is_cs2(summary: GridSeriesSummary) -> bool:
    title_name = normalize_name(summary.title_name or "")
    return "counterstrike" in title_name or "cs2" in title_name or "csgo" in title_name


def _series_team_names(summary: GridSeriesSummary) -> list[str]:
    names = []
    for team in summary.teams:
        base = team.get("baseInfo") or {}
        name = base.get("name")
        if name:
            names.append(name)
    return names


def _series_team_grid_ids(summary: GridSeriesSummary) -> list[str]:
    ids = []
    for team in summary.teams:
        base = team.get("baseInfo") or {}
        grid_id = base.get("id")
        if grid_id:
            ids.append(str(grid_id))
    return ids


def _series_involves_top_team(summary: GridSeriesSummary, top_names: set[str]) -> bool:
    if not _series_is_cs2(summary):
        return False
    for name in _series_team_names(summary):
        if normalize_name(name) in top_names:
            return True
    return False


def _series_involves_any_team(summary: GridSeriesSummary, team_names: set[str]) -> bool:
    if not _series_is_cs2(summary):
        return False
    for name in _series_team_names(summary):
        if normalize_name(name) in team_names:
            return True
    return False


def _scheduled_match_from_summary(summary: GridSeriesSummary, session: Session) -> MatchDTO | None:
    summary_teams = [_team_from_grid(team, session) for team in summary.teams]
    summary_teams = [team for team in summary_teams if team]
    if len(summary_teams) < 2:
        return None
    return MatchDTO(
        hltv_match_id=stable_negative_id(f"grid-series:{summary.id}"),
        source_url=f"grid://series/{summary.id}",
        match_time=datetime.fromisoformat(summary.start_time_scheduled.replace("Z", "+00:00")).replace(tzinfo=None) if summary.start_time_scheduled else None,
        status="scheduled",
        team1=summary_teams[0],
        team2=summary_teams[1],
        winner_hltv_team_id=None,
        event_name=summary.tournament_name,
    )


def _state_flags(state: dict[str, Any]) -> tuple[bool, bool, bool]:
    games = state.get("games") or []
    has_games = bool(games)
    has_maps = any(bool(game.get("map")) for game in games)
    has_players = any(bool(team.get("players")) for game in games for team in (game.get("teams") or []))
    return has_games, has_maps, has_players


def save_raw_grid_state(session: Session, series_id: str, state: dict[str, Any]) -> GridRawSeriesState:
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    existing = session.scalar(select(GridRawSeriesState).where(GridRawSeriesState.content_hash == content_hash))
    if existing:
        return existing
    has_games, has_maps, has_players = _state_flags(state)
    row = GridRawSeriesState(
        grid_series_id=series_id,
        content_hash=content_hash,
        has_games=has_games,
        has_maps=has_maps,
        has_players=has_players,
        payload_json=payload,
    )
    session.add(row)
    return row


def upsert_grid_entity_map(
    session: Session,
    entity_type: str,
    grid_id: str | None,
    *,
    local_table: str | None = None,
    local_id: int | None = None,
    name: str | None = None,
) -> GridEntityMap | None:
    if not grid_id:
        return None
    normalized_grid_id = str(grid_id)
    for item in session.new:
        if isinstance(item, GridEntityMap) and item.entity_type == entity_type and item.grid_id == normalized_grid_id:
            row = item
            break
    else:
        row = session.scalar(select(GridEntityMap).where(GridEntityMap.entity_type == entity_type, GridEntityMap.grid_id == normalized_grid_id))
    if row is None:
        row = GridEntityMap(entity_type=entity_type, grid_id=normalized_grid_id)
        session.add(row)
    row.local_table = local_table or row.local_table
    row.local_id = local_id or row.local_id
    row.name = name or row.name
    return row


def save_grid_identity_maps(session: Session, summary: GridSeriesSummary, state: dict[str, Any], dto: MatchDTO | None = None) -> None:
    upsert_grid_entity_map(session, "series", summary.id, local_table="matches", name=summary.tournament_name)
    upsert_grid_entity_map(session, "title", summary.title_id, name=summary.title_name)
    for team_node in summary.teams:
        base = team_node.get("baseInfo") or {}
        name = base.get("name")
        local_id = None
        if name:
            team = find_team_by_alias(session, name)
            local_id = team.id if team else None
        upsert_grid_entity_map(session, "team", base.get("id"), local_table="teams", local_id=local_id, name=name)
    for game in state.get("games") or []:
        upsert_grid_entity_map(session, "game", game.get("id"), local_table="match_maps", name=(game.get("map") or {}).get("name"))
        for team_state in game.get("teams") or []:
            name = team_state.get("name")
            local_id = None
            if name:
                team = find_team_by_alias(session, name)
                local_id = team.id if team else None
            upsert_grid_entity_map(session, "team", team_state.get("id"), local_table="teams", local_id=local_id, name=name)
            for player_state in team_state.get("players") or []:
                player_id, player_name = _player_identity(player_state)
                local_id = None
                if player_id:
                    player = session.scalar(select(Player).where(Player.hltv_player_id == stable_negative_id(f"grid-player:{player_id}")))
                    local_id = player.id if player else None
                upsert_grid_entity_map(session, "player", player_id, local_table="players", local_id=local_id, name=player_name)


def _player_stats(game: dict[str, Any], map_number: int, session: Session, summary_teams: list[TeamDTO]) -> list[PlayerStatDTO]:
    rows: list[PlayerStatDTO] = []
    rounds = _round_count(game)
    for team_index, team_state in enumerate(game.get("teams") or []):
        team_dto = summary_teams[team_index] if team_index < len(summary_teams) else None
        for player_state in team_state.get("players") or []:
            player_id, name = _player_identity(player_state)
            if not player_id or not name:
                continue
            kills = player_state.get("kills")
            deaths = player_state.get("deaths")
            assists = player_state.get("killAssistsGiven")
            if assists is None:
                assists = player_state.get("assists")
            damage_dealt = player_state.get("damageDealt")
            headshots = player_state.get("headshots")
            rows.append(
                PlayerStatDTO(
                    map_number=map_number,
                    hltv_player_id=stable_negative_id(f"grid-player:{player_id}"),
                    nickname=name,
                    hltv_team_id=team_dto.hltv_team_id if team_dto else None,
                    kills=kills,
                    deaths=deaths,
                    assists=assists,
                    kd_diff=(kills - deaths) if kills is not None and deaths is not None else None,
                    kd_ratio=calculate_kd_ratio(kills, deaths),
                    adr=_adr(damage_dealt, rounds),
                    headshot_percentage=_headshot_percentage(kills, headshots),
                )
            )
    return rows


def grid_state_to_match(summary: GridSeriesSummary, state: dict[str, Any], session: Session) -> MatchDTO | None:
    summary_teams = [_team_from_grid(team, session) for team in summary.teams]
    summary_teams = [team for team in summary_teams if team]
    state_teams = state.get("teams") or []
    if len(summary_teams) < 2 and len(state_teams) >= 2:
        summary_teams = [_team_dto_for_state(t, session) for t in state_teams[:2]]
        summary_teams = [team for team in summary_teams if team]
    if len(summary_teams) < 2:
        return None

    winner_id = None
    for team_index, team_state in enumerate(state_teams):
        if team_state.get("won"):
            winner_id = summary_teams[team_index].hltv_team_id if team_index < len(summary_teams) else None
    if winner_id is None and len(state_teams) >= 2:
        score1 = state_teams[0].get("score")
        score2 = state_teams[1].get("score")
        if score1 is not None and score2 is not None and score1 != score2:
            winner_id = summary_teams[0].hltv_team_id if score1 > score2 else summary_teams[1].hltv_team_id

    maps: list[MapDTO] = []
    stats: list[PlayerStatDTO] = []
    for index, game in enumerate(state.get("games") or [], start=1):
        teams = game.get("teams") or []
        if len(teams) < 2:
            continue
        score1 = teams[0].get("score")
        score2 = teams[1].get("score")
        if score1 is None or score2 is None:
            continue
        game_winner = None
        for team_index, team_state in enumerate(teams):
            if team_state.get("won"):
                game_winner = summary_teams[team_index].hltv_team_id if team_index < len(summary_teams) else None
        if game_winner is None and score1 != score2:
            game_winner = summary_teams[0].hltv_team_id if score1 > score2 else summary_teams[1].hltv_team_id
        maps.append(
            MapDTO(
                map_number=index,
                name=normalize_map_name((game.get("map") or {}).get("name")),
                hltv_mapstats_id=stable_negative_id(f"grid-game:{game['id']}"),
                score_team1=score1,
                score_team2=score2,
                winner_hltv_team_id=game_winner,
            )
        )
        stats.extend(_player_stats(game, index, session, summary_teams))

    status = "scheduled"
    if state.get("finished") and (winner_id is not None or maps):
        status = "completed"
    elif state.get("started"):
        status = "live"

    return MatchDTO(
        hltv_match_id=stable_negative_id(f"grid-series:{summary.id}"),
        source_url=f"grid://series/{summary.id}",
        match_time=datetime.fromisoformat(summary.start_time_scheduled.replace("Z", "+00:00")).replace(tzinfo=None) if summary.start_time_scheduled else None,
        status=status,
        team1=summary_teams[0],
        team2=summary_teams[1],
        winner_hltv_team_id=winner_id,
        event_name=summary.tournament_name,
        score_team1=state_teams[0].get("score") if len(state_teams) >= 2 else None,
        score_team2=state_teams[1].get("score") if len(state_teams) >= 2 else None,
        maps=maps,
        player_stats=stats,
    )


def ingest_recent_grid_series(
    session: Session,
    client: GridClient,
    date_from: datetime,
    date_to: datetime,
    max_pages: int,
    max_matches: int,
    dry_run: bool = False,
    top_limit: int = 30,
    require_top_team: bool = True,
) -> dict[str, int]:
    top_names = latest_top_team_names(session, top_limit)
    if require_top_team and not top_names:
        raise RuntimeError(f"No top-{top_limit} ranking snapshot found. Load ranking first.")
    repo = AnalyticsRepository(session)
    page = 0
    after = None
    checked = matched = saved = skipped = errors = 0
    while page < max_pages and saved < max_matches:
        summaries, page_info = client.list_series(date_from, date_to, first=50, after=after)
        page += 1
        for summary in summaries:
            checked += 1
            if require_top_team:
                matched_series = _series_involves_top_team(summary, top_names)
            else:
                matched_series = _series_is_cs2(summary)
            if not matched_series:
                skipped += 1
                continue
            matched += 1
            if dry_run:
                continue
            try:
                state = client.series_state(summary.id)
                save_raw_grid_state(session, summary.id, state)
                dto = grid_state_to_match(summary, state, session)
            except GridApiError as exc:
                logger.warning("Skipping GRID series %s: %s", summary.id, exc)
                errors += 1
                skipped += 1
                continue
            if dto is None:
                skipped += 1
                continue
            repo.save_match(dto)
            save_grid_identity_maps(session, summary, state, dto)
            saved += 1
            if saved >= max_matches:
                break
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    return {
        "pages": page,
        "checked": checked,
        "matched_top30": matched,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "new_matches": repo.new_matches,
        "updated_matches": repo.updated_matches,
    }


def ingest_grid_history_for_team_names(
    session: Session,
    client: GridClient,
    team_names: set[str],
    date_from: datetime,
    date_to: datetime,
    max_pages: int,
    max_matches: int,
    dry_run: bool = False,
) -> dict[str, int]:
    normalized_names = {normalize_name(name) for name in team_names if name}
    repo = AnalyticsRepository(session)
    page = 0
    after = None
    checked = matched = saved = skipped = errors = 0
    while page < max_pages and saved < max_matches:
        summaries, page_info = client.list_series(date_from, date_to, first=50, after=after)
        page += 1
        for summary in summaries:
            checked += 1
            if not _series_involves_any_team(summary, normalized_names):
                skipped += 1
                continue
            matched += 1
            if dry_run:
                continue
            try:
                state = client.series_state(summary.id)
                save_raw_grid_state(session, summary.id, state)
                dto = grid_state_to_match(summary, state, session)
            except GridApiError as exc:
                logger.warning("Skipping GRID history series %s: %s", summary.id, exc)
                errors += 1
                skipped += 1
                continue
            if dto is None:
                skipped += 1
                continue
            repo.save_match(dto)
            save_grid_identity_maps(session, summary, state, dto)
            saved += 1
            if saved >= max_matches:
                break
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    return {
        "pages": page,
        "checked": checked,
        "matched": matched,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "new_matches": repo.new_matches,
        "updated_matches": repo.updated_matches,
    }


def ingest_grid_history_for_team_ids(
    session: Session,
    client: GridClient,
    team_ids: set[str],
    date_from: datetime,
    date_to: datetime,
    max_pages: int,
    max_matches: int,
    dry_run: bool = False,
) -> dict[str, int]:
    normalized_ids = {str(team_id) for team_id in team_ids if team_id}
    repo = AnalyticsRepository(session)
    page = 0
    checked = matched = saved = skipped = errors = 0
    if not normalized_ids:
        return {"pages": 0, "checked": 0, "matched": 0, "saved": 0, "skipped": 0, "errors": 0, "new_matches": 0, "updated_matches": 0}
    for team_id in sorted(normalized_ids):
        after = None
        while page < max_pages and saved < max_matches:
            summaries, page_info = client.list_series(date_from, date_to, first=50, after=after, team_ids=[team_id])
            page += 1
            for summary in summaries:
                checked += 1
                if not _series_is_cs2(summary):
                    skipped += 1
                    continue
                matched += 1
                if dry_run:
                    continue
                try:
                    state = client.series_state(summary.id)
                    save_raw_grid_state(session, summary.id, state)
                    dto = grid_state_to_match(summary, state, session)
                except GridApiError as exc:
                    logger.warning("Skipping GRID team history series %s: %s", summary.id, exc)
                    errors += 1
                    skipped += 1
                    continue
                if dto is None:
                    skipped += 1
                    continue
                repo.save_match(dto)
                save_grid_identity_maps(session, summary, state, dto)
                saved += 1
                if saved >= max_matches:
                    break
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
        if page >= max_pages or saved >= max_matches:
            break
    return {
        "pages": page,
        "checked": checked,
        "matched": matched,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "new_matches": repo.new_matches,
        "updated_matches": repo.updated_matches,
    }


def _grid_series_id_from_source(source_url: str | None) -> str | None:
    prefix = "grid://series/"
    if source_url and source_url.startswith(prefix):
        return source_url[len(prefix):]
    return None


def _local_match_summary(session: Session, match: Match) -> GridSeriesSummary | None:
    series_id = _grid_series_id_from_source(match.source_url)
    if not series_id:
        return None
    team1 = session.get(Team, match.team1_id) if match.team1_id else None
    team2 = session.get(Team, match.team2_id) if match.team2_id else None
    if team1 is None or team2 is None:
        return None
    event = session.get(Event, match.event_id) if match.event_id else None
    team_nodes = []
    for team in [team1, team2]:
        entity = session.scalar(select(GridEntityMap).where(GridEntityMap.entity_type == "team", GridEntityMap.local_id == team.id).order_by(desc(GridEntityMap.updated_at)).limit(1))
        team_nodes.append({"baseInfo": {"id": entity.grid_id if entity else f"local-{team.id}", "name": team.name}})
    return GridSeriesSummary(
        id=series_id,
        start_time_scheduled=match.match_time.strftime("%Y-%m-%dT%H:%M:%SZ") if match.match_time else None,
        tournament_name=event.name if event else None,
        title_name="Counter Strike 2",
        teams=team_nodes,
    )


def refresh_live_grid_matches(
    session: Session,
    client: GridClient,
    *,
    limit: int = 50,
    dry_run: bool = False,
) -> dict[str, int]:
    repo = AnalyticsRepository(session)
    now = datetime.now(UTC).replace(tzinfo=None)
    matches = session.scalars(
        select(Match)
        .where(
            Match.source_url.like("grid://series/%"),
            (Match.status.in_(["scheduled", "live"])) | (Match.match_time >= now),
        )
        .order_by(Match.match_time.asc(), Match.id.asc())
        .limit(limit)
    ).all()
    checked = refreshed = skipped = errors = state_errors = completed = 0
    for match in matches:
        checked += 1
        summary = _local_match_summary(session, match)
        if summary is None:
            skipped += 1
            continue
        if dry_run:
            refreshed += 1
            continue
        try:
            state = client.series_state(summary.id)
            save_raw_grid_state(session, summary.id, state)
        except GridApiError as exc:
            logger.warning("GRID live refresh state unavailable for %s: %s", summary.id, exc)
            state_errors += 1
            skipped += 1
            continue
        dto = grid_state_to_match(summary, state, session)
        if dto is None:
            skipped += 1
            continue
        repo.save_match(dto)
        save_grid_identity_maps(session, summary, state, dto)
        refreshed += 1
        if dto.status == "completed":
            completed += 1
    return {
        "checked": checked,
        "refreshed": refreshed,
        "skipped": skipped,
        "errors": errors,
        "state_errors": state_errors,
        "completed": completed,
        "new_matches": repo.new_matches,
        "updated_matches": repo.updated_matches,
    }


def ingest_upcoming_grid_series(
    session: Session,
    client: GridClient,
    date_from: datetime,
    date_to: datetime,
    max_pages: int,
    max_matches: int,
    *,
    top_limit: int = 50,
    dry_run: bool = False,
    history_days: int = 90,
    history_max_pages: int = 20,
    history_max_matches: int = 200,
) -> dict[str, object]:
    top_names = latest_top_team_names(session, top_limit)
    if not top_names:
        raise RuntimeError(f"No top-{top_limit} ranking snapshot found. Load ranking first.")
    repo = AnalyticsRepository(session)
    page = 0
    after = None
    checked = matched = saved = skipped = errors = state_errors = 0
    involved_names: set[str] = set()
    involved_grid_team_ids: set[str] = set()
    while page < max_pages and saved < max_matches:
        summaries, page_info = client.list_series(date_from, date_to, first=50, after=after)
        page += 1
        for summary in summaries:
            checked += 1
            if not _series_involves_top_team(summary, top_names):
                skipped += 1
                continue
            matched += 1
            for name in _series_team_names(summary):
                involved_names.add(name)
            involved_grid_team_ids.update(_series_team_grid_ids(summary))
            if dry_run:
                continue
            state: dict[str, Any] = {}
            dto: MatchDTO | None = None
            try:
                state = client.series_state(summary.id)
                save_raw_grid_state(session, summary.id, state)
                dto = grid_state_to_match(summary, state, session)
            except GridApiError as exc:
                logger.warning("GRID upcoming seriesState unavailable for %s; saving scheduled summary only: %s", summary.id, exc)
                state_errors += 1
            if dto is None:
                dto = _scheduled_match_from_summary(summary, session)
            if dto is None:
                errors += 1
                skipped += 1
                continue
            repo.save_match(dto)
            save_grid_identity_maps(session, summary, state, dto)
            saved += 1
            if saved >= max_matches:
                break
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")

    history_result: dict[str, int] | None = None
    if history_days > 0 and involved_names and history_max_matches > 0:
        history_to = date_from
        history_from = history_to - timedelta(days=history_days)
        if involved_grid_team_ids:
            history_result = ingest_grid_history_for_team_ids(
                session=session,
                client=client,
                team_ids=involved_grid_team_ids,
                date_from=history_from,
                date_to=history_to,
                max_pages=history_max_pages,
                max_matches=history_max_matches,
                dry_run=dry_run,
            )
        else:
            history_result = ingest_grid_history_for_team_names(
                session=session,
                client=client,
                team_names=involved_names,
                date_from=history_from,
                date_to=history_to,
                max_pages=history_max_pages,
                max_matches=history_max_matches,
                dry_run=dry_run,
            )

    return {
        "pages": page,
        "checked": checked,
        "matched_top50": matched,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "state_errors": state_errors,
        "new_matches": repo.new_matches,
        "updated_matches": repo.updated_matches,
        "involved_teams": sorted(involved_names),
        "involved_grid_team_ids": sorted(involved_grid_team_ids),
        "history": history_result,
    }

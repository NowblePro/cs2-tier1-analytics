from __future__ import annotations

import uuid
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, or_, select, union_all
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.analytics import estimate_backfill, grid_stats_summary, pre_match_edge
from app.dust2 import Dust2Client, sync_missing_dust2_rounds
from app.grid import GridClient, audit_grid_period, ingest_grid_history_for_team_ids, ingest_recent_grid_series, ingest_upcoming_grid_series, refresh_live_grid_matches, refresh_saved_grid_matches, reset_backfill_days, run_grid_backfill, run_grid_update_since_cursor
from app.grid.client import GridApiError
from app.grid.ingest import _series_is_cs2, normalize_name
from app.repositories.team_aliases import canonical_team_key, merge_team_aliases
from app.grid.stats import refresh_grid_stats
from app.jobs import create_job_run, run_post_sync_pipeline, serialize_job_run, update_job_run
from app.metrics import compute_metrics
from app.models.schema import AutomationSetting, Event, ExternalEntityMap, GridBackfillDay, GridEntityMap, GridRawSeriesState, GridStatsSnapshot, GridSyncCursor, JobRun, Match, MatchMap, Player, PlayerMapStat, RankingSnapshot, RankingSnapshotTeam, Round, Team, TeamRollingMetric
from app.pandascore import PandaScoreClient, ingest_past_pandascore_results, ingest_team_pandascore_history, ingest_upcoming_with_histories
from app.quality import period_quality_report
from app.valve_vrs import ValveVrsClient, ingest_latest_valve_ranking
from app.update_all import run_update_all
from app.web.routers import operations_router, system_router
from app.web.database import session_factory
from app.web.job_runtime import JobCoordinator, PeriodicScheduler
from app.web.schemas import AutomationRequest, BackfillResetRequest, GridStatsRefreshRequest, GridSyncRequest

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _job_coordinator.start()
    recover_interrupted_jobs()
    _automation_scheduler.start()
    try:
        yield
    finally:
        _automation_scheduler.stop()
        _job_coordinator.stop()


app = FastAPI(title="CS2 Tier-1 Analytics", lifespan=lifespan)
app.include_router(system_router)
app.include_router(operations_router)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
logger = logging.getLogger(__name__)
EXCLUDED_ANALYTICS_EVENTS = {"GRID-TEST"}
TIER_1_EVENT_KEYWORDS = (
    "major",
    "blast",
    "iem",
    "katowice",
    "cologne",
    "esl pro league",
    "esl impact",
    "epl",
    "pgl",
    "starladder",
    "betboom dacha",
    "cs asia championships",
    "esports world cup",
)
TIER_2_EVENT_KEYWORDS = (
    "cct",
    "thunderpick",
    "ya lla",
    "res",
    "skyesports",
    "elisa",
    "fireshow",
    "perfect world",
)


def _included_event():
    return Event.name.is_(None) | Event.name.not_in(EXCLUDED_ANALYTICS_EVENTS)


def _event_priority(event_name: str | None) -> dict[str, Any]:
    name = (event_name or "").strip()
    lowered = name.lower()
    if not name:
        return {"tier": "unknown", "priority": 0, "label": "No event"}
    if any(keyword in lowered for keyword in TIER_1_EVENT_KEYWORDS):
        return {"tier": "tier-1", "priority": 100, "label": "Tier-1"}
    if any(keyword in lowered for keyword in TIER_2_EVENT_KEYWORDS):
        return {"tier": "tier-2", "priority": 60, "label": "Tier-2"}
    return {"tier": "other", "priority": 20, "label": "Other"}


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _date_range(payload: GridSyncRequest) -> tuple[datetime, datetime]:
    now = datetime.now(UTC).replace(tzinfo=None)
    if payload.mode in {"upcoming", "pandascore-upcoming"} and payload.date_from is None and payload.date_to is None:
        date_from = now
        date_to = now + timedelta(days=payload.days)
    else:
        date_to = _naive_utc(payload.date_to) if payload.date_to else now
        date_from = _naive_utc(payload.date_from) if payload.date_from else date_to - timedelta(days=payload.days)
    if date_from >= date_to:
        raise ValueError("date_from must be earlier than date_to")
    return date_from, date_to


def _ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if denominator in (None, 0) or numerator is None:
        return None
    return round(float(numerator) / float(denominator), 4)


def _match_completeness(session, match: Match, maps: list[MatchMap] | None = None) -> dict[str, Any]:
    maps = maps if maps is not None else list(session.scalars(select(MatchMap).where(MatchMap.match_id == match.id)))
    map_ids = [item.id for item in maps]
    player_stats = session.scalar(select(func.count(PlayerMapStat.id)).where(PlayerMapStat.match_map_id.in_(map_ids))) if map_ids else 0
    rounds = session.scalar(select(func.count(Round.id)).where(Round.match_map_id.in_(map_ids))) if map_ids else 0
    flags = {
        "schedule": bool(match.match_time and match.team1_id and match.team2_id),
        "result": bool(match.score_team1 is not None and match.score_team2 is not None and match.winner_team_id),
        "maps": bool(maps and all(item.score_team1 is not None and item.score_team2 is not None for item in maps)),
        "players": bool(player_stats),
        "rounds": bool(rounds),
    }
    level = "schedule"
    for candidate in ("result", "maps", "players", "rounds"):
        if flags[candidate]:
            level = candidate
    return {"level": level, "flags": flags, "player_stats": player_stats or 0, "rounds": rounds or 0}


def _resolve_team_grid_id(
    session,
    client: GridClient,
    team: Team,
    date_from: datetime,
    date_to: datetime,
    max_pages: int,
    progress=None,
) -> str:
    mappings = session.scalars(
        select(GridEntityMap)
        .where(GridEntityMap.entity_type == "team")
        .order_by(desc(GridEntityMap.updated_at), desc(GridEntityMap.id))
    ).all()
    target_key = canonical_team_key(team.name)
    candidate_ids: list[str] = []
    for mapping in mappings:
        if mapping.local_id == team.id or canonical_team_key(mapping.name) == target_key:
            if mapping.grid_id and not mapping.grid_id.startswith("local-") and mapping.grid_id not in candidate_ids:
                candidate_ids.append(mapping.grid_id)
    for grid_id in candidate_ids:
        try:
            sample, _ = client.list_series(date_from, date_to, first=1, team_ids=[grid_id])
            if not sample or any(_series_is_cs2(item) for item in sample):
                return grid_id
        except GridApiError as exc:
            if "Invalid teamId" not in str(exc):
                raise

    after = None
    for page in range(1, max_pages + 1):
        summaries, page_info = client.list_series(date_from, date_to, first=50, after=after)
        if progress:
            progress({"stage": "resolve-team", "page": page, "pages_limit": max_pages, "checked": page * 50, "team": team.name})
        for summary in summaries:
            if not _series_is_cs2(summary):
                continue
            for node in summary.teams:
                base = node.get("baseInfo") or {}
                if canonical_team_key(base.get("name")) != target_key or not base.get("id"):
                    continue
                grid_id = str(base["id"])
                mapping = session.scalar(select(GridEntityMap).where(GridEntityMap.entity_type == "team", GridEntityMap.grid_id == grid_id))
                if mapping is None:
                    mapping = GridEntityMap(entity_type="team", grid_id=grid_id)
                    session.add(mapping)
                mapping.local_table = "teams"
                mapping.local_id = team.id
                mapping.name = team.name
                return grid_id
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
    raise GridApiError(f"Could not resolve a valid GRID ID for team '{team.name}' in the selected period")


def _team_recent_metrics(session, team_id: int, limit: int) -> dict[str, Any]:
    matches = session.scalars(
        select(Match)
        .outerjoin(Event, Match.event_id == Event.id)
        .where(((Match.team1_id == team_id) | (Match.team2_id == team_id)), Match.status == "completed")
        .where(_included_event())
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(limit)
    ).all()
    match_ids = [match.id for match in matches]
    won_matches = sum(1 for match in matches if match.winner_team_id == team_id)
    maps = session.scalars(select(MatchMap).where(MatchMap.match_id.in_(match_ids))).all() if match_ids else []
    played_maps = [item for item in maps if item.score_team1 is not None and item.score_team2 is not None]
    won_maps = sum(1 for item in played_maps if item.winner_team_id == team_id)
    stat_totals = session.execute(
        select(func.sum(PlayerMapStat.kills), func.sum(PlayerMapStat.deaths), func.avg(PlayerMapStat.adr))
        .join(MatchMap, PlayerMapStat.match_map_id == MatchMap.id)
        .where(PlayerMapStat.team_id == team_id, MatchMap.match_id.in_(match_ids))
    ).one() if match_ids else (None, None, None)
    map_breakdown = []
    for name in sorted({item.name for item in played_maps}):
        named_maps = [item for item in played_maps if item.name == name]
        map_breakdown.append(
            {
                "map": name,
                "played": len(named_maps),
                "win_rate": _ratio(sum(1 for item in named_maps if item.winner_team_id == team_id), len(named_maps)),
            }
        )
    return {
        "window_matches": limit,
        "matches_played": len(matches),
        "match_win_rate": _ratio(won_matches, len(matches)),
        "maps_played": len(played_maps),
        "map_win_rate": _ratio(won_maps, len(played_maps)),
        "kd_ratio": _ratio(stat_totals[0], stat_totals[1]),
        "avg_adr": round(float(stat_totals[2]), 2) if stat_totals[2] is not None else None,
        "map_breakdown": map_breakdown,
    }


def _team_recent_matches(session, team_id: int, limit: int = 10) -> list[dict[str, Any]]:
    Team1 = aliased(Team)
    Team2 = aliased(Team)
    rows = session.execute(
        select(Match, Event, Team1, Team2)
        .outerjoin(Event, Match.event_id == Event.id)
        .join(Team1, Match.team1_id == Team1.id)
        .join(Team2, Match.team2_id == Team2.id)
        .where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        .where(Match.status == "completed")
        .where(_included_event())
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(limit)
    ).all()
    result = []
    for match, event, team1, team2 in rows:
        maps = session.scalars(select(MatchMap).where(MatchMap.match_id == match.id).order_by(MatchMap.map_number)).all()
        event_name = event.name if event else None
        result.append({
            "id": match.id,
            "match_time": match.match_time.isoformat() if match.match_time else None,
            "status": match.status,
            "event": event_name,
            "event_priority": _event_priority(event_name),
            "team1": {"id": team1.id, "name": team1.name},
            "team2": {"id": team2.id, "name": team2.name},
            "score_team1": match.score_team1,
            "score_team2": match.score_team2,
            "won": match.winner_team_id == team_id if match.winner_team_id else None,
            "completeness": _match_completeness(session, match, maps),
            "maps": [
                {
                    "number": item.map_number,
                    "name": item.name,
                    "score_team1": item.score_team1,
                    "score_team2": item.score_team2,
                }
                for item in maps
            ],
        })
    return result


def _team_players(session, team_id: int, limit: int = 10) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Player,
            func.count(PlayerMapStat.id),
            func.sum(PlayerMapStat.kills),
            func.sum(PlayerMapStat.deaths),
            func.sum(PlayerMapStat.assists),
            func.avg(PlayerMapStat.adr),
            func.avg(PlayerMapStat.headshot_percentage),
        )
        .outerjoin(PlayerMapStat, PlayerMapStat.player_id == Player.id)
        .where(Player.current_team_id == team_id)
        .group_by(Player.id)
        .order_by(desc(func.count(PlayerMapStat.id)), Player.nickname)
        .limit(limit)
    ).all()
    return [
        {
            "id": player.id,
            "nickname": player.nickname,
            "maps": maps or 0,
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "kd_ratio": _ratio(kills, deaths),
            "avg_adr": round(float(avg_adr), 2) if avg_adr is not None else None,
            "headshot_percentage": round(float(hs), 2) if hs is not None else None,
        }
        for player, maps, kills, deaths, assists, avg_adr, hs in rows
    ]


def _team_player_form(session, team_id: int, match_limit: int) -> list[dict[str, Any]]:
    match_ids = list(session.scalars(
        select(Match.id)
        .where(((Match.team1_id == team_id) | (Match.team2_id == team_id)), Match.status == "completed")
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(match_limit)
    ))
    if not match_ids:
        return []
    rows = session.execute(
        select(
            Player,
            func.count(PlayerMapStat.id),
            func.sum(PlayerMapStat.kills),
            func.sum(PlayerMapStat.deaths),
            func.sum(PlayerMapStat.assists),
            func.avg(PlayerMapStat.adr),
        )
        .join(PlayerMapStat, PlayerMapStat.player_id == Player.id)
        .join(MatchMap, PlayerMapStat.match_map_id == MatchMap.id)
        .where(PlayerMapStat.team_id == team_id, MatchMap.match_id.in_(match_ids))
        .group_by(Player.id)
        .order_by(desc(func.sum(PlayerMapStat.kills)))
    ).all()
    return [
        {
            "id": player.id,
            "nickname": player.nickname,
            "maps": maps,
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "kd_ratio": _ratio(kills, deaths),
            "avg_adr": round(float(adr), 2) if adr is not None else None,
        }
        for player, maps, kills, deaths, assists, adr in rows
    ]


def _team_ranked_opponent_performance(session, team_id: int, match_limit: int = 50) -> list[dict[str, Any]]:
    snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.id)).limit(1))
    ranks = dict(session.execute(
        select(RankingSnapshotTeam.team_id, RankingSnapshotTeam.rank).where(RankingSnapshotTeam.snapshot_id == snapshot.id)
    ).all()) if snapshot else {}
    matches = session.scalars(
        select(Match)
        .where(((Match.team1_id == team_id) | (Match.team2_id == team_id)), Match.status == "completed")
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(match_limit)
    ).all()
    result = []
    for threshold in (10, 30, 50):
        selected = []
        for match in matches:
            opponent_id = match.team2_id if match.team1_id == team_id else match.team1_id
            if opponent_id is not None and ranks.get(opponent_id, 10_000) <= threshold:
                selected.append(match)
        wins = sum(match.winner_team_id == team_id for match in selected)
        result.append({"top": threshold, "matches": len(selected), "wins": wins, "win_rate": _ratio(wins, len(selected))})
    return result


def _team_upcoming(session, team_id: int, limit: int = 10) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(tzinfo=None)
    Team1 = aliased(Team)
    Team2 = aliased(Team)
    rows = session.execute(
        select(Match, Event, Team1, Team2)
        .outerjoin(Event, Match.event_id == Event.id)
        .join(Team1, Match.team1_id == Team1.id)
        .join(Team2, Match.team2_id == Team2.id)
        .where((Match.team1_id == team_id) | (Match.team2_id == team_id), Match.match_time >= now)
        .order_by(Match.match_time, Match.id)
        .limit(limit)
    ).all()
    return [
        {
            "id": match.id,
            "match_time": match.match_time.isoformat() if match.match_time else None,
            "event": event.name if event else None,
            "status": match.status,
            "team1": {"id": team1.id, "name": team1.name},
            "team2": {"id": team2.id, "name": team2.name},
            "completeness": _match_completeness(session, match),
        }
        for match, event, team1, team2 in rows
    ]


def _team_map_pool(session, team_id: int, limit: int) -> list[dict[str, Any]]:
    matches = session.scalars(
        select(Match)
        .outerjoin(Event, Match.event_id == Event.id)
        .where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        .where(_included_event())
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(limit)
    ).all()
    by_name: dict[str, dict[str, Any]] = {}
    if not matches:
        return []
    maps = session.scalars(select(MatchMap).where(MatchMap.match_id.in_([match.id for match in matches]))).all()
    match_by_id = {match.id: match for match in matches}
    for item in maps:
        if item.score_team1 is None or item.score_team2 is None:
            continue
        match = match_by_id.get(item.match_id)
        if match is None:
            continue
        if match.team1_id == team_id:
            rounds_for, rounds_against = item.score_team1, item.score_team2
        else:
            rounds_for, rounds_against = item.score_team2, item.score_team1
        row = by_name.setdefault(item.name, {"map": item.name, "played": 0, "wins": 0, "round_diff": 0})
        row["played"] += 1
        row["round_diff"] += int(rounds_for or 0) - int(rounds_against or 0)
        if item.winner_team_id == team_id:
            row["wins"] += 1
    result = []
    for row in by_name.values():
        result.append(
            {
                "map": row["map"],
                "played": row["played"],
                "win_rate": _ratio(row["wins"], row["played"]),
                "round_diff": row["round_diff"],
            }
        )
    return sorted(result, key=lambda row: (-row["played"], row["map"]))


def _comparison_metric_rows(team1: dict[str, Any], team2: dict[str, Any]) -> list[dict[str, Any]]:
    metrics1 = team1.get("metrics") or {}
    metrics2 = team2.get("metrics") or {}
    grid1 = grid_stats_summary(team1.get("grid_stats"))
    grid2 = grid_stats_summary(team2.get("grid_stats"))
    rows = [
        ("Series win rate", metrics1.get("match_win_rate"), metrics2.get("match_win_rate"), "percent"),
        ("Map win rate", metrics1.get("map_win_rate"), metrics2.get("map_win_rate"), "percent"),
        ("K/D", metrics1.get("kd_ratio"), metrics2.get("kd_ratio"), "number"),
        ("ADR", metrics1.get("avg_adr"), metrics2.get("avg_adr"), "number"),
        ("T round win rate", metrics1.get("t_round_win_rate"), metrics2.get("t_round_win_rate"), "percent"),
        ("CT round win rate", metrics1.get("ct_round_win_rate"), metrics2.get("ct_round_win_rate"), "percent"),
        ("Pistol win rate", metrics1.get("pistol_win_rate"), metrics2.get("pistol_win_rate"), "percent"),
        ("GRID series WR", grid1.get("series_win_rate"), grid2.get("series_win_rate"), "grid_percent"),
        ("First kill", grid1.get("first_kill_rate"), grid2.get("first_kill_rate"), "grid_percent"),
    ]
    return [
        {
            "label": label,
            "team1_value": value1,
            "team2_value": value2,
            "unit": unit,
            "leader": "team1" if value1 is not None and (value2 is None or value1 > value2) else "team2" if value2 is not None and (value1 is None or value2 > value1) else "tie",
        }
        for label, value1, value2, unit in rows
    ]


def _map_pool_comparison(team1_maps: list[dict[str, Any]], team2_maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    maps1 = {row["map"]: row for row in team1_maps}
    maps2 = {row["map"]: row for row in team2_maps}
    result = []
    for name in sorted(set(maps1) | set(maps2)):
        row1 = maps1.get(name) or {}
        row2 = maps2.get(name) or {}
        value1 = row1.get("win_rate")
        value2 = row2.get("win_rate")
        result.append(
            {
                "map": name,
                "team1_win_rate": value1,
                "team2_win_rate": value2,
                "team1_sample": row1.get("played", 0),
                "team2_sample": row2.get("played", 0),
                "team1_round_diff": row1.get("round_diff"),
                "team2_round_diff": row2.get("round_diff"),
                "state": "insufficient_sample" if (row1.get("played", 0) < 3 or row2.get("played", 0) < 3) else "ready",
                "leader": "team1" if value1 is not None and (value2 is None or value1 > value2) else "team2" if value2 is not None and (value1 is None or value2 > value1) else "tie",
            }
        )
    return result


def _coverage(team: dict[str, Any]) -> dict[str, Any]:
    metrics = team.get("metrics") or {}
    players = team.get("players") or []
    grid_summary = grid_stats_summary(team.get("grid_stats"))
    matches = int(metrics.get("matches_played") or 0)
    maps = int(metrics.get("maps_played") or 0)
    players_with_stats = sum(1 for player in players if (player.get("maps") or 0) > 0)
    grid_series = int(grid_summary.get("series_count") or 0)
    score = min(100, matches * 4 + maps * 2 + players_with_stats * 5 + grid_series * 3)
    level = "high" if score >= 70 else "medium" if score >= 35 else "low"
    warnings = []
    if matches < 5:
        warnings.append("low recent match sample")
    if players_with_stats < 5:
        warnings.append("incomplete player sample")
    if grid_series == 0:
        warnings.append("no GRID stats snapshot")
    return {
        "score": score,
        "level": level,
        "matches": matches,
        "maps": maps,
        "players_with_stats": players_with_stats,
        "grid_series": grid_series,
        "warnings": warnings,
    }


def _opponent_records(session, team_id: int, limit: int) -> dict[int, dict[str, Any]]:
    rows = session.scalars(
        select(Match)
        .outerjoin(Event, Match.event_id == Event.id)
        .where((Match.team1_id == team_id) | (Match.team2_id == team_id), Match.winner_team_id.is_not(None), _included_event())
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(limit)
    ).all()
    records: dict[int, dict[str, Any]] = {}
    for match in rows:
        opponent_id = match.team2_id if match.team1_id == team_id else match.team1_id
        if opponent_id is None:
            continue
        row = records.setdefault(opponent_id, {"opponent_id": opponent_id, "games": 0, "wins": 0})
        row["games"] += 1
        row["wins"] += int(match.winner_team_id == team_id)
    return records


def _matchup_context(session, team1: Team, team2: Team, window: int) -> dict[str, Any]:
    h2h = session.scalars(
        select(Match)
        .where(
            or_(
                (Match.team1_id == team1.id) & (Match.team2_id == team2.id),
                (Match.team1_id == team2.id) & (Match.team2_id == team1.id),
            ),
            Match.winner_team_id.is_not(None),
        )
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(window)
    ).all()
    records1 = _opponent_records(session, team1.id, window)
    records2 = _opponent_records(session, team2.id, window)
    common = []
    for opponent_id in set(records1) & set(records2):
        opponent = session.get(Team, opponent_id)
        row1, row2 = records1[opponent_id], records2[opponent_id]
        common.append({
            "opponent_id": opponent_id,
            "opponent": opponent.name if opponent else str(opponent_id),
            "team1_games": row1["games"],
            "team1_win_rate": _ratio(row1["wins"], row1["games"]),
            "team2_games": row2["games"],
            "team2_win_rate": _ratio(row2["wins"], row2["games"]),
        })
    common.sort(key=lambda row: (-(row["team1_games"] + row["team2_games"]), row["opponent"]))
    return {
        "head_to_head": {
            "matches": len(h2h),
            "team1_wins": sum(match.winner_team_id == team1.id for match in h2h),
            "team2_wins": sum(match.winner_team_id == team2.id for match in h2h),
        },
        "common_opponents": common[:10],
    }


def _preview_payload(session, team1: Team, team2: Team, window: int, stats_window: str) -> dict[str, Any]:
    team1_maps = _team_map_pool(session, team1.id, window)
    team2_maps = _team_map_pool(session, team2.id, window)
    team1_recent = _team_recent_matches(session, team1.id, 5)
    team2_recent = _team_recent_matches(session, team2.id, 5)
    team1_payload = {
        "id": team1.id,
        "name": team1.name,
        "metrics": _team_recent_metrics(session, team1.id, window),
        "recent_matches": team1_recent,
        "form": ["W" if item.get("won") else "L" for item in team1_recent],
        "grid_stats": _team_grid_stats(session, team1.id, team1.name, stats_window),
        "players": _team_players(session, team1.id, 5),
        "map_pool": team1_maps,
    }
    team2_payload = {
        "id": team2.id,
        "name": team2.name,
        "metrics": _team_recent_metrics(session, team2.id, window),
        "recent_matches": team2_recent,
        "form": ["W" if item.get("won") else "L" for item in team2_recent],
        "grid_stats": _team_grid_stats(session, team2.id, team2.name, stats_window),
        "players": _team_players(session, team2.id, 5),
        "map_pool": team2_maps,
    }
    coverage1 = _coverage(team1_payload)
    coverage2 = _coverage(team2_payload)
    warnings = [f"{team1.name}: {item}" for item in coverage1["warnings"]] + [f"{team2.name}: {item}" for item in coverage2["warnings"]]
    context = _matchup_context(session, team1, team2, window)
    metric_rows = _comparison_metric_rows(team1_payload, team2_payload)
    map_rows = _map_pool_comparison(team1_maps, team2_maps)
    advantages = {
        "team1": [
            {"type": "metric", "label": row["label"], "value": row["team1_value"], "other_value": row["team2_value"], "unit": row["unit"]}
            for row in metric_rows if row["leader"] == "team1"
        ][:4],
        "team2": [
            {"type": "metric", "label": row["label"], "value": row["team2_value"], "other_value": row["team1_value"], "unit": row["unit"]}
            for row in metric_rows if row["leader"] == "team2"
        ][:4],
    }
    return {
        "window": window,
        "stats_window": stats_window,
        "team1": team1_payload,
        "team2": team2_payload,
        "edge": pre_match_edge(team1_payload, team2_payload),
        "metrics": metric_rows,
        "map_pool": map_rows,
        "advantages": advantages,
        "player_form": {"team1": team1_payload["players"], "team2": team2_payload["players"]},
        "coverage": {"team1": coverage1, "team2": coverage2, "warnings": warnings},
        **context,
    }


def _team_grid_stats(session, team_id: int, team_name: str | None = None, window: str = "LAST_MONTH") -> dict[str, Any] | None:
    row = session.scalar(
        select(GridStatsSnapshot)
        .where(
            GridStatsSnapshot.entity_type == "team",
            GridStatsSnapshot.local_id == team_id,
            GridStatsSnapshot.window_name == window,
        )
        .order_by(desc(GridStatsSnapshot.fetched_at))
        .limit(1)
    )
    if row is None and team_name:
        row = session.scalar(
            select(GridStatsSnapshot)
            .where(
                GridStatsSnapshot.entity_type == "team",
                GridStatsSnapshot.name == team_name,
                GridStatsSnapshot.window_name == window,
            )
            .order_by(desc(GridStatsSnapshot.fetched_at))
            .limit(1)
        )
    if row is None and team_name:
        target = normalize_name(team_name)
        candidates = session.scalars(
            select(GridStatsSnapshot)
            .where(GridStatsSnapshot.entity_type == "team", GridStatsSnapshot.window_name == window)
            .order_by(desc(GridStatsSnapshot.fetched_at))
        ).all()
        for candidate in candidates:
            candidate_name = normalize_name(candidate.name or "")
            without_team_prefix = candidate_name[4:] if candidate_name.startswith("team") else candidate_name
            if candidate_name == target or without_team_prefix == target:
                row = candidate
                break
    if row is None:
        return None
    return {
        "grid_id": row.grid_id,
        "name": row.name,
        "window": row.window_name,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "stats": json.loads(row.payload_json),
    }


def _team_coverage_snapshot(session, team_id: int) -> dict[str, Any]:
    match_ids = list(session.scalars(
        select(Match.id)
        .outerjoin(Event, Match.event_id == Event.id)
        .where(((Match.team1_id == team_id) | (Match.team2_id == team_id)), Match.status == "completed", _included_event())
    ))
    total = len(match_ids)
    with_maps = session.scalar(
        select(func.count(func.distinct(MatchMap.match_id))).where(MatchMap.match_id.in_(match_ids))
    ) if match_ids else 0
    with_players = session.scalar(
        select(func.count(func.distinct(MatchMap.match_id)))
        .join(PlayerMapStat, PlayerMapStat.match_map_id == MatchMap.id)
        .where(MatchMap.match_id.in_(match_ids))
    ) if match_ids else 0
    with_rounds = session.scalar(
        select(func.count(func.distinct(MatchMap.match_id)))
        .join(Round, Round.match_map_id == MatchMap.id)
        .where(MatchMap.match_id.in_(match_ids))
    ) if match_ids else 0
    return {
        "matches": total,
        "result_only": total - int(with_maps or 0),
        "with_maps": int(with_maps or 0),
        "with_players": int(with_players or 0),
        "with_rounds": int(with_rounds or 0),
        "map_coverage": _ratio(with_maps, total),
        "player_coverage": _ratio(with_players, total),
        "round_coverage": _ratio(with_rounds, total),
    }


def _persist_job_update(job_id: str, updates: dict[str, Any]) -> None:
    Session = session_factory()
    with Session.begin() as session:
        update_job_run(
            session,
            job_id,
            status=updates.get("status"),
            result=updates.get("result"),
            error=updates.get("error"),
            progress=updates.get("progress"),
            started="started_at" in updates,
            finished="finished_at" in updates,
        )


_job_coordinator = JobCoordinator(
    on_update=_persist_job_update,
    stale_after_seconds=max(1, get_settings().job_stale_minutes) * 60,
)


def _set_job(job_id: str, **updates: Any) -> None:
    _job_coordinator.set_job(job_id, **updates)


def _set_job_progress(job_id: str, progress: dict[str, object]) -> None:
    # The sync transaction may hold SQLite's write lock. Keep frequent progress
    # updates in memory and persist the final snapshot after that transaction.
    _job_coordinator.set_progress(job_id, progress)


def _run_grid_sync_job(job_id: str, payload: GridSyncRequest) -> None:
    _set_job(job_id, status="running", started_at=datetime.now(UTC).isoformat())
    settings = get_settings()
    client = None
    pandascore_client = None
    valve_client = None
    cancel_event = _job_coordinator.cancel_event(job_id)
    try:
        date_from, date_to = _date_range(payload)
        if payload.mode not in {"pandascore-upcoming", "pandascore-results", "valve-ranking", "update-all"}:
            client = GridClient(settings)
        Session = session_factory()
        session_context = Session() if payload.mode == "backfill" else Session.begin()
        with session_context as session:
            if payload.mode == "valve-ranking":
                valve_client = ValveVrsClient(settings)
                result = ingest_latest_valve_ranking(
                    session,
                    valve_client,
                    limit=payload.top_limit,
                    dry_run=payload.dry_run,
                    progress=lambda item: _set_job_progress(job_id, item),
                )
            elif payload.mode == "update-all":
                valve_client = ValveVrsClient(settings)
                pandascore_client = PandaScoreClient(settings)
                client = GridClient(settings)
                result = run_update_all(
                    session,
                    valve_client=valve_client,
                    pandascore_client=pandascore_client,
                    grid_client=client,
                    top_limit=payload.top_limit,
                    upcoming_days=payload.history_days or 14,
                    results_days=payload.days,
                    participant_history_days=payload.participant_history_days,
                    max_matches=payload.max_matches,
                    dry_run=payload.dry_run,
                    refresh_stats=payload.refresh_stats,
                    progress=lambda item: _set_job_progress(job_id, item),
                    should_cancel=cancel_event.is_set,
                )
            elif payload.mode == "match":
                if payload.match_id is None:
                    raise ValueError("match_id is required for match sync")
                target_match = session.get(Match, payload.match_id)
                if target_match is None or target_match.match_time is None or target_match.team1_id is None:
                    raise ValueError("Match is missing its date or teams")
                team = session.get(Team, target_match.team1_id)
                if team is None:
                    raise ValueError("Match team was not found")
                grid_id = _resolve_team_grid_id(
                    session,
                    client,
                    team,
                    target_match.match_time - timedelta(hours=12),
                    target_match.match_time + timedelta(hours=12),
                    payload.max_pages,
                    progress=lambda item: _set_job_progress(job_id, {"phase": "resolve", **item}),
                )
                result = ingest_grid_history_for_team_ids(
                    session=session,
                    client=client,
                    team_ids={grid_id},
                    date_from=target_match.match_time - timedelta(hours=12),
                    date_to=target_match.match_time + timedelta(hours=12),
                    max_pages=payload.max_pages,
                    max_matches=20,
                    dry_run=payload.dry_run,
                    skip_completed=False,
                    progress=lambda item: _set_job_progress(job_id, {"phase": "grid", **item}),
                    should_cancel=cancel_event.is_set,
                )
                result["match_id"] = payload.match_id
            elif payload.mode == "repair":
                quality = period_quality_report(
                    session,
                    date_from,
                    date_to,
                    candidate_limit=payload.max_matches,
                )
                match_ids = [int(item["match_id"]) for item in quality["repair_candidates"]]
                repair = refresh_saved_grid_matches(
                    session,
                    client,
                    match_ids,
                    dry_run=payload.dry_run,
                    progress=lambda item: _set_job_progress(job_id, item),
                    should_cancel=cancel_event.is_set,
                )
                result = {
                    "quality_before": {
                        "matches": quality["matches"],
                        "levels": quality["levels"],
                        "map_coverage": quality["map_coverage"],
                        "player_coverage": quality["player_coverage"],
                        "round_coverage": quality["round_coverage"],
                        "repairable_count": quality["repairable_count"],
                    },
                    "repair": repair,
                }
            elif payload.mode == "team":
                if payload.team_id is None:
                    raise ValueError("team_id is required for team sync")
                team = session.get(Team, payload.team_id)
                if team is None:
                    raise ValueError("Team not found")
                coverage_before = _team_coverage_snapshot(session, team.id)
                pandascore_client = PandaScoreClient(settings)
                pandascore_result = ingest_team_pandascore_history(
                    session,
                    pandascore_client,
                    team,
                    date_from,
                    date_to,
                    max_pages=payload.max_pages,
                    max_matches=payload.max_matches,
                    dry_run=payload.dry_run,
                    progress=lambda item: _set_job_progress(job_id, {"phase": "pandascore", **item}),
                    should_cancel=cancel_event.is_set,
                )
                try:
                    grid_id = _resolve_team_grid_id(
                        session,
                        client,
                        team,
                        date_from,
                        date_to,
                        payload.max_pages,
                        progress=lambda item: _set_job_progress(job_id, {"phase": "grid", **item}),
                    )
                    grid_result = ingest_grid_history_for_team_ids(
                        session=session,
                        client=client,
                        team_ids={grid_id},
                        date_from=date_from,
                        date_to=date_to,
                        max_pages=payload.max_pages,
                        max_matches=payload.max_matches,
                        dry_run=payload.dry_run,
                        skip_completed=not payload.force_refresh,
                        progress=lambda item: _set_job_progress(job_id, {"phase": "grid", **item}),
                        should_cancel=cancel_event.is_set,
                    )
                except (GridApiError, RuntimeError, ValueError) as exc:
                    grid_result = {"provider": "grid", "saved": 0, "error": str(exc)}
                coverage_after = _team_coverage_snapshot(session, team.id)
                coverage_delta = {
                    key: int(coverage_after.get(key) or 0) - int(coverage_before.get(key) or 0)
                    for key in ("matches", "result_only", "with_maps", "with_players", "with_rounds")
                }
                result = {
                    "pandascore": pandascore_result,
                    "grid": grid_result,
                    "coverage_before": coverage_before,
                    "coverage_after": coverage_after,
                    "coverage_delta": coverage_delta,
                    "summary": {
                        "pandascore_checked": pandascore_result.get("checked", 0),
                        "pandascore_saved": pandascore_result.get("saved", 0),
                        "pandascore_new": pandascore_result.get("new_matches", 0),
                        "pandascore_updated": pandascore_result.get("updated_matches", 0),
                        "grid_matched": grid_result.get("matched", 0),
                        "grid_detailed": grid_result.get("saved", 0),
                        "grid_skipped_existing": grid_result.get("skipped_existing", 0),
                        "errors": int(pandascore_result.get("errors", 0) or 0) + int(grid_result.get("errors", 0) or 0),
                    },
                }
                result["team_id"] = team.id
                result["team"] = team.name
                result["days"] = payload.days
            elif payload.mode == "backfill":
                result = run_grid_backfill(
                    session=session,
                    client=client,
                    date_from=date_from,
                    date_to=date_to,
                    cursor_name=payload.cursor,
                    window_days=payload.window_days,
                    max_pages_per_window=payload.max_pages,
                    max_matches_per_window=payload.max_matches,
                    top_limit=payload.top_limit,
                    require_top_team=payload.require_top_team,
                    dry_run=payload.dry_run,
                    progress=lambda item: _set_job_progress(job_id, item),
                    checkpoint=session.commit,
                    should_cancel=cancel_event.is_set,
                )
            elif payload.mode == "update":
                result = run_grid_update_since_cursor(
                    session=session,
                    client=client,
                    cursor_name=payload.cursor,
                    fallback_days=payload.days,
                    max_pages=payload.max_pages,
                    max_matches=payload.max_matches,
                    top_limit=payload.top_limit,
                    require_top_team=payload.require_top_team,
                    dry_run=payload.dry_run,
                )
            elif payload.mode == "pandascore-upcoming":
                pandascore_client = PandaScoreClient(settings)
                result = ingest_upcoming_with_histories(
                    session=session,
                    client=pandascore_client,
                    date_from=date_from,
                    date_to=date_to,
                    max_pages=payload.max_pages,
                    max_matches=payload.max_matches,
                    history_days=payload.participant_history_days,
                    history_max_pages=payload.history_max_pages,
                    history_max_matches=payload.history_max_matches,
                    top_limit=payload.top_limit,
                    dry_run=payload.dry_run,
                    progress=lambda item: _set_job_progress(job_id, item),
                    should_cancel=cancel_event.is_set,
                )
            elif payload.mode == "pandascore-results":
                pandascore_client = PandaScoreClient(settings)
                result = ingest_past_pandascore_results(
                    session=session,
                    client=pandascore_client,
                    date_from=date_from,
                    date_to=date_to,
                    max_pages=payload.max_pages,
                    max_matches=payload.max_matches,
                    top_limit=payload.top_limit,
                    dry_run=payload.dry_run,
                    progress=lambda item: _set_job_progress(job_id, item),
                    should_cancel=cancel_event.is_set,
                )
            elif payload.mode == "upcoming":
                result = ingest_upcoming_grid_series(
                    session=session,
                    client=client,
                    date_from=date_from,
                    date_to=date_to,
                    max_pages=payload.max_pages,
                    max_matches=payload.max_matches,
                    top_limit=payload.top_limit,
                    dry_run=payload.dry_run,
                    history_days=payload.history_days,
                    history_max_pages=payload.history_max_pages,
                    history_max_matches=payload.history_max_matches,
                    should_cancel=cancel_event.is_set,
                )
            elif payload.mode == "audit":
                result = audit_grid_period(
                    session=session,
                    client=client,
                    date_from=date_from,
                    date_to=date_to,
                    max_pages=payload.max_pages,
                    top_limit=payload.top_limit,
                    require_top_team=payload.require_top_team,
                    progress=lambda item: _set_job_progress(job_id, item),
                    should_cancel=cancel_event.is_set,
                )
            elif payload.mode == "refresh-live":
                result = refresh_live_grid_matches(
                    session=session,
                    client=client,
                    limit=payload.max_matches,
                    dry_run=payload.dry_run,
                    should_cancel=cancel_event.is_set,
                )
            else:
                result = ingest_recent_grid_series(
                    session=session,
                    client=client,
                    date_from=date_from,
                    date_to=date_to,
                    max_pages=payload.max_pages,
                    max_matches=payload.max_matches,
                    dry_run=payload.dry_run,
                    top_limit=payload.top_limit,
                    require_top_team=payload.require_top_team,
                    should_cancel=cancel_event.is_set,
                )
            cancelled = cancel_event.is_set() or bool(result.get("cancelled"))
            if not payload.dry_run and payload.mode != "audit" and not cancelled:
                if payload.mode not in {"team", "match", "update-all"} and payload.post_pipeline:
                    pipeline = run_post_sync_pipeline(
                        session,
                        client,
                        stats_window=payload.stats_window,
                        stats_limit=payload.top_limit,
                        refresh_stats_enabled=payload.refresh_stats,
                        progress=lambda item: _set_job_progress(job_id, item),
                        should_cancel=cancel_event.is_set,
                    )
                    result = {"sync": result, "post_pipeline": pipeline}
                else:
                    if payload.mode in {"team", "match"}:
                        result["cleanup"] = merge_team_aliases(session, dry_run=False)
                        dust2_client = Dust2Client()
                        try:
                            if payload.mode == "match" and payload.match_id is not None:
                                match_ids = [payload.match_id]
                                rounds_limit = 1
                            elif payload.mode == "team" and payload.team_id is not None:
                                match_ids = list(session.scalars(
                                    select(Match.id)
                                    .where(
                                        ((Match.team1_id == payload.team_id) | (Match.team2_id == payload.team_id)),
                                        Match.status == "completed",
                                        Match.match_time >= date_from,
                                        Match.match_time <= date_to,
                                    )
                                    .order_by(desc(Match.match_time), desc(Match.id))
                                    .limit(min(payload.max_matches, 50))
                                ))
                                rounds_limit = min(payload.max_matches, 50)
                            else:
                                match_ids = None
                                rounds_limit = 25
                            result["rounds"] = sync_missing_dust2_rounds(
                                session=session,
                                client=dust2_client,
                                match_ids=match_ids,
                                limit=rounds_limit,
                                progress=lambda item: _set_job_progress(job_id, item),
                                should_cancel=cancel_event.is_set,
                            )
                        finally:
                            dust2_client.close()
                    compute_metrics(session)
            if payload.mode == "backfill":
                session.commit()
        final_progress = _job_coordinator.get_progress(job_id)
        sync_result = result.get("sync", result) if isinstance(result, dict) else {}
        if cancel_event.is_set() or bool(sync_result.get("cancelled")):
            status = "cancelled"
        elif payload.mode == "backfill" and not sync_result.get("complete", False):
            status = "partial"
        else:
            status = "completed"
        _set_job(job_id, status=status, result=result, progress=final_progress, finished_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        final_progress = _job_coordinator.get_progress(job_id)
        _set_job(job_id, status="failed", error=str(exc), progress=final_progress, finished_at=datetime.now(UTC).isoformat())
    finally:
        if valve_client:
            valve_client.close()
        if pandascore_client:
            pandascore_client.close()
        if client:
            client.close()


def _start_grid_sync_thread(payload: GridSyncRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    metadata = {
        "job_id": job_id,
        "status": "queued",
        "dry_run": payload.dry_run,
        "created_at": datetime.now(UTC).isoformat(),
        "request": payload.model_dump(mode="json"),
        "kind": f"grid-{payload.mode}",
    }
    Session = session_factory()
    with Session.begin() as session:
        create_job_run(session, job_id=job_id, kind=f"grid-{payload.mode}", request=payload.model_dump(mode="json"))
    _job_coordinator.enqueue("grid", job_id, payload, metadata)
    return {"ok": True, "job_id": job_id, "status": "queued"}


def _recovery_payload(kind: str, request_json: str | None) -> GridSyncRequest | None:
    request = json.loads(request_json) if request_json else {}
    if request.get("trigger") not in {"automation", "recovery"} or not kind.startswith("grid-"):
        return None
    return GridSyncRequest(**{**request, "trigger": "recovery"})


def recover_interrupted_jobs() -> dict[str, int]:
    Session = session_factory()
    recoverable: list[GridSyncRequest] = []
    interrupted = 0
    with Session.begin() as session:
        rows = session.scalars(
            select(JobRun)
            .where(JobRun.status.in_(["queued", "running", "cancelling"]))
            .order_by(JobRun.created_at)
        ).all()
        for row in rows:
            interrupted += 1
            try:
                payload = _recovery_payload(row.kind, row.request_json)
                if payload:
                    recoverable.append(payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("Could not restore job %s because its request is invalid", row.job_id)
            update_job_run(
                session,
                row.job_id,
                status="interrupted",
                error="Server restarted before the job completed",
                finished=True,
            )
    for payload in recoverable:
        _start_grid_sync_thread(payload)
    if interrupted:
        logger.warning("Marked %s interrupted jobs; requeued %s automation jobs", interrupted, len(recoverable))
    return {"interrupted": interrupted, "requeued": len(recoverable)}


def _automation_row(session) -> AutomationSetting:
    row = session.get(AutomationSetting, 1)
    if row is None:
        row = AutomationSetting(id=1)
        session.add(row)
        session.flush()
    return row


def _automation_tick() -> None:
    Session = session_factory()
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session.begin() as session:
        setting = _automation_row(session)
        if not setting.enabled or (setting.next_run_at and setting.next_run_at > now):
            return
        if _job_coordinator.active_job():
            return
        setting.last_started_at = now
        setting.next_run_at = now + timedelta(minutes=setting.interval_minutes)
        payload = GridSyncRequest(
            mode="update-all",
            days=setting.results_days,
            history_days=setting.upcoming_days,
            top_limit=setting.top_limit,
            max_matches=setting.max_matches,
            refresh_stats=setting.refresh_stats,
            post_pipeline=False,
            participant_history_days=180,
            trigger="automation",
        )
    _start_grid_sync_thread(payload)


_automation_scheduler = PeriodicScheduler(_automation_tick)


def _run_grid_stats_job(job_id: str, payload: GridStatsRefreshRequest) -> None:
    _set_job(job_id, status="running", started_at=datetime.now(UTC).isoformat())
    settings = get_settings()
    client = None
    cancel_event = _job_coordinator.cancel_event(job_id)
    try:
        client = GridClient(settings)
        Session = session_factory()
        with Session.begin() as session:
            result = refresh_grid_stats(
                session=session,
                client=client,
                entity_type=payload.entity_type,
                window_name=payload.window,
                limit=payload.limit,
                dry_run=payload.dry_run,
                progress=lambda item: _set_job_progress(job_id, item),
                should_cancel=cancel_event.is_set,
            )
        status = "cancelled" if cancel_event.is_set() or result.get("cancelled") else "completed"
        _set_job(job_id, status=status, result=result, finished_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc), finished_at=datetime.now(UTC).isoformat())
    finally:
        if client:
            client.close()


def _start_grid_stats_thread(payload: GridStatsRefreshRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    metadata = {
        "job_id": job_id,
        "status": "queued",
        "dry_run": payload.dry_run,
        "created_at": datetime.now(UTC).isoformat(),
        "request": payload.model_dump(mode="json"),
        "kind": "grid-stats",
    }
    Session = session_factory()
    with Session.begin() as session:
        create_job_run(session, job_id=job_id, kind="grid-stats", request=payload.model_dump(mode="json"))
    _job_coordinator.enqueue("stats", job_id, payload, metadata)
    return {"ok": True, "job_id": job_id, "status": "queued"}


_job_coordinator.register_runner("grid", _run_grid_sync_job)
_job_coordinator.register_runner("stats", _run_grid_stats_job)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/summary")
def summary():
    Session = session_factory()
    with Session() as session:
        latest_snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.id)).limit(1))
        ranking_teams = 0
        ranked_teams_with_matches = 0
        if latest_snapshot:
            ranked_team_ids = list(session.scalars(select(RankingSnapshotTeam.team_id).where(RankingSnapshotTeam.snapshot_id == latest_snapshot.id)))
            ranking_teams = len(ranked_team_ids)
            if ranked_team_ids:
                ranked_teams_with_matches = session.scalar(
                    select(func.count(func.distinct(Team.id))).where(
                        Team.id.in_(ranked_team_ids),
                        ((Team.id.in_(select(Match.team1_id).where(Match.status == "completed"))) | (Team.id.in_(select(Match.team2_id).where(Match.status == "completed")))),
                    )
                ) or 0
        return {
            "teams": session.scalar(select(func.count(Team.id))) or 0,
            "players": session.scalar(select(func.count(Player.id))) or 0,
            "matches": session.scalar(select(func.count(Match.id))) or 0,
            "maps": session.scalar(select(func.count(MatchMap.id))) or 0,
            "player_stats": session.scalar(select(func.count(PlayerMapStat.id))) or 0,
            "grid_raw_snapshots": session.scalar(select(func.count(GridRawSeriesState.id))) or 0,
            "grid_entity_maps": session.scalar(select(func.count(GridEntityMap.id))) or 0,
            "grid_stats_snapshots": session.scalar(select(func.count(GridStatsSnapshot.id))) or 0,
            "ranking_teams": ranking_teams,
            "ranked_teams_with_matches": ranked_teams_with_matches,
            "latest_ranking_date": latest_snapshot.ranking_date.isoformat() if latest_snapshot else None,
        }


@app.get("/api/teams")
def teams(limit: int = 30, window: int = 20, stats_window: str = "LAST_MONTH"):
    limit = max(1, min(limit, 100))
    window = max(1, min(window, 100))
    Session = session_factory()
    with Session() as session:
        latest_snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.id)).limit(1))
        ranking_rows = []
        if latest_snapshot:
            ranking_rows = session.execute(
                select(Team, RankingSnapshotTeam, TeamRollingMetric)
                .join(RankingSnapshotTeam, RankingSnapshotTeam.team_id == Team.id)
                .outerjoin(TeamRollingMetric, (TeamRollingMetric.team_id == Team.id) & (TeamRollingMetric.window_name == "all"))
                .where(RankingSnapshotTeam.snapshot_id == latest_snapshot.id)
                .order_by(RankingSnapshotTeam.rank)
                .limit(limit)
            ).all()
        team_ids = [team.id for team, _, _ in ranking_rows]
        match_summary: dict[int, dict[str, Any]] = {}
        if team_ids:
            team_matches = union_all(
                select(Match.team1_id.label("team_id"), Match.id.label("match_id"), Match.match_time.label("match_time"), Match.winner_team_id.label("winner_team_id")).outerjoin(Event, Match.event_id == Event.id).where(Match.team1_id.in_(team_ids), Match.status == "completed", _included_event()),
                select(Match.team2_id.label("team_id"), Match.id.label("match_id"), Match.match_time.label("match_time"), Match.winner_team_id.label("winner_team_id")).outerjoin(Event, Match.event_id == Event.id).where(Match.team2_id.in_(team_ids), Match.status == "completed", _included_event()),
            ).subquery()
            match_rows = session.execute(
                select(
                    team_matches.c.team_id,
                    func.count(func.distinct(team_matches.c.match_id)),
                    func.max(team_matches.c.match_time),
                ).group_by(team_matches.c.team_id)
            ).all()
            match_summary = {
                team_id: {"matches": match_count, "last_played": last_played}
                for team_id, match_count, last_played in match_rows
            }
            for team_id, winner_team_id in session.execute(
                select(team_matches.c.team_id, team_matches.c.winner_team_id)
                .order_by(team_matches.c.team_id, desc(team_matches.c.match_time), desc(team_matches.c.match_id))
            ):
                form = match_summary.setdefault(team_id, {}).setdefault("form", [])
                if len(form) < 5:
                    form.append("W" if winner_team_id == team_id else "L")
        result = []
        for team, rank, metric in ranking_rows:
            recent = _team_recent_metrics(session, team.id, window)
            grid_summary = grid_stats_summary(_team_grid_stats(session, team.id, team.name, stats_window))
            result.append({
                "id": team.id,
                "name": team.name,
                "rank": rank.rank,
                "points": rank.points,
                "matches": match_summary.get(team.id, {}).get("matches", 0),
                "last_played": match_summary.get(team.id, {}).get("last_played").isoformat() if match_summary.get(team.id, {}).get("last_played") else None,
                "form": match_summary.get(team.id, {}).get("form", []),
                "window_matches": recent["matches_played"],
                "match_win_rate": recent["match_win_rate"],
                "map_win_rate": recent["map_win_rate"],
                "kd_ratio": recent["kd_ratio"],
                "pistol_win_rate": metric.pistol_win_rate if metric else None,
                "grid_series_count": grid_summary.get("series_count"),
                "grid_series_win_rate": grid_summary.get("series_win_rate"),
                "stats_window": stats_window,
            })
        return result


@app.get("/api/teams/{team_id}")
def team_detail(team_id: int, window: int = 20, stats_window: str = "LAST_MONTH"):
    Session = session_factory()
    with Session() as session:
        team = session.get(Team, team_id)
        if team is None:
            return {"ok": False, "error": "Team not found"}
        metric = session.scalar(select(TeamRollingMetric).where(TeamRollingMetric.team_id == team.id, TeamRollingMetric.window_name == "all"))
        grid_stats = _team_grid_stats(session, team.id, team.name, stats_window)
        recent = _team_recent_metrics(session, team.id, window)
        form_windows = {str(size): _team_recent_metrics(session, team.id, size) for size in (5, 10, 20, 50)}
        latest_snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.id)).limit(1))
        ranking = session.scalar(
            select(RankingSnapshotTeam)
            .where(RankingSnapshotTeam.snapshot_id == latest_snapshot.id, RankingSnapshotTeam.team_id == team.id)
            .limit(1)
        ) if latest_snapshot else None
        matches_with_maps = session.scalar(
            select(func.count(func.distinct(MatchMap.match_id)))
            .join(Match, MatchMap.match_id == Match.id)
            .where(((Match.team1_id == team.id) | (Match.team2_id == team.id)), Match.status == "completed")
        ) or 0
        return {
            "ok": True,
            "team": {
                "id": team.id,
                "name": team.name,
                "country": team.country,
                "logo_url": team.logo_url,
                "rank": ranking.rank if ranking else None,
                "points": ranking.points if ranking else None,
                "ranking_date": latest_snapshot.ranking_date.isoformat() if latest_snapshot else None,
                "matches": recent["matches_played"],
                "matches_with_maps": matches_with_maps,
                "results_only_matches": max(0, recent["matches_played"] - matches_with_maps),
                "maps_played": recent["maps_played"],
                "metric": {
                    "matches_played": metric.matches_played if metric else 0,
                    "match_win_rate": metric.match_win_rate if metric else None,
                    "map_win_rate": metric.map_win_rate if metric else None,
                    "kd_ratio": metric.kd_ratio if metric else None,
                    "t_round_win_rate": metric.t_round_win_rate if metric else None,
                    "ct_round_win_rate": metric.ct_round_win_rate if metric else None,
                    "pistol_win_rate": metric.pistol_win_rate if metric else None,
                },
                "recent": recent,
                "recent_matches": _team_recent_matches(session, team.id, window),
                "players": _team_players(session, team.id, 10),
                "player_form": _team_player_form(session, team.id, window),
                "form_windows": form_windows,
                "ranked_opponents": _team_ranked_opponent_performance(session, team.id),
                "upcoming_matches": _team_upcoming(session, team.id),
                "grid_stats": grid_stats,
                "grid_summary": grid_stats_summary(grid_stats),
            },
        }


@app.get("/api/compare")
def compare_teams(team1_id: int, team2_id: int, window: int = 20, stats_window: str = "LAST_MONTH"):
    Session = session_factory()
    with Session() as session:
        team1 = session.get(Team, team1_id)
        team2 = session.get(Team, team2_id)
        if team1 is None or team2 is None:
            return {"ok": False, "error": "Team not found"}
        return {"ok": True, **_preview_payload(session, team1, team2, window, stats_window)}


@app.get("/api/teams/{team_id}/players")
def team_players(team_id: int, limit: int = 10):
    limit = max(1, min(limit, 100))
    Session = session_factory()
    with Session() as session:
        team = session.get(Team, team_id)
        if team is None:
            return {"ok": False, "error": "Team not found"}
        return {"ok": True, "team": {"id": team.id, "name": team.name}, "players": _team_players(session, team.id, limit)}


@app.get("/api/matches")
def matches(
    page: int = 1,
    page_size: int = 50,
    limit: int | None = None,
    status: str = "completed",
    days: int | None = None,
    team_id: int | None = None,
    map_name: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    detail_level: str = "all",
):
    page = max(1, page)
    page_size = max(1, min(limit if limit is not None else page_size, 200))
    if date_from is None and days is not None:
        date_from = datetime.now(UTC) - timedelta(days=max(1, min(days, 3650)))
    Session = session_factory()
    with Session() as session:
        Team1 = aliased(Team)
        Team2 = aliased(Team)
        query = (
            select(Match, Event, Team1, Team2)
            .outerjoin(Event, Match.event_id == Event.id)
            .join(Team1, Match.team1_id == Team1.id)
            .join(Team2, Match.team2_id == Team2.id)
            .where(_included_event())
        )
        if team_id is not None:
            query = query.where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        if status != "all":
            allowed_statuses = {"completed", "live", "scheduled"}
            if status not in allowed_statuses:
                return {"items": [], "total": 0, "page": page, "page_size": page_size, "pages": 0, "error": "Unknown match status"}
            query = query.where(Match.status == status)
        if date_from is not None:
            query = query.where(Match.match_time >= _naive_utc(date_from))
        if date_to is not None:
            query = query.where(Match.match_time <= _naive_utc(date_to))
        if map_name:
            query = query.join(MatchMap, MatchMap.match_id == Match.id).where(MatchMap.name == map_name)
        map_match_ids = select(MatchMap.match_id)
        player_match_ids = select(MatchMap.match_id).join(PlayerMapStat, PlayerMapStat.match_map_id == MatchMap.id)
        round_match_ids = select(MatchMap.match_id).join(Round, Round.match_map_id == MatchMap.id)
        if detail_level == "result_only":
            query = query.where(Match.id.not_in(map_match_ids))
        elif detail_level == "maps":
            query = query.where(Match.id.in_(map_match_ids))
        elif detail_level == "players":
            query = query.where(Match.id.in_(player_match_ids))
        elif detail_level == "rounds":
            query = query.where(Match.id.in_(round_match_ids))
        elif detail_level != "all":
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "pages": 0, "error": "Unknown detail level"}
        query = query.distinct()
        count_query = select(func.count()).select_from(query.with_only_columns(Match.id).order_by(None).subquery())
        total = session.scalar(count_query) or 0
        rows = session.execute(
            query.order_by(desc(Match.match_time), desc(Match.id)).offset((page - 1) * page_size).limit(page_size)
        ).all()
        result = []
        for match, event, team1, team2 in rows:
            maps = session.scalars(select(MatchMap).where(MatchMap.match_id == match.id).order_by(MatchMap.map_number)).all()
            event_name = event.name if event else None
            result.append(
                {
                    "id": match.id,
                    "source_url": match.source_url,
                    "match_time": match.match_time.isoformat() if match.match_time else None,
                    "status": match.status,
                    "event": event_name,
                    "event_priority": _event_priority(event_name),
                    "best_of": match.best_of,
                    "format": f"BO{match.best_of}" if match.best_of else None,
                    "team1": {"id": team1.id, "name": team1.name},
                    "team2": {"id": team2.id, "name": team2.name},
                    "score_team1": match.score_team1,
                    "score_team2": match.score_team2,
                    "completeness": _match_completeness(session, match, maps),
                    "maps": [
                        {
                            "number": item.map_number,
                            "name": item.name,
                            "score_team1": item.score_team1,
                            "score_team2": item.score_team2,
                        }
                        for item in maps
                    ],
                }
            )
        return {
            "items": result,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }


@app.get("/api/data-coverage")
def data_coverage(
    team_id: int | None = None,
    days: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    Session = session_factory()
    with Session() as session:
        query = (
            select(Match.id)
            .outerjoin(Event, Match.event_id == Event.id)
            .where(Match.status == "completed", _included_event())
        )
        if team_id is not None:
            query = query.where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        if date_from is not None:
            query = query.where(Match.match_time >= _naive_utc(date_from))
        elif days is not None:
            query = query.where(Match.match_time >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=max(1, min(days, 3650))))
        if date_to is not None:
            query = query.where(Match.match_time <= _naive_utc(date_to))
        match_ids = list(session.scalars(query))
        total = len(match_ids)
        with_maps = session.scalar(select(func.count(func.distinct(MatchMap.match_id))).where(MatchMap.match_id.in_(match_ids))) if match_ids else 0
        with_players = session.scalar(select(func.count(func.distinct(MatchMap.match_id))).join(PlayerMapStat, PlayerMapStat.match_map_id == MatchMap.id).where(MatchMap.match_id.in_(match_ids))) if match_ids else 0
        with_rounds = session.scalar(select(func.count(func.distinct(MatchMap.match_id))).join(Round, Round.match_map_id == MatchMap.id).where(MatchMap.match_id.in_(match_ids))) if match_ids else 0
        return {
            "matches": total,
            "result_only": total - int(with_maps or 0),
            "with_maps": int(with_maps or 0),
            "with_players": int(with_players or 0),
            "with_rounds": int(with_rounds or 0),
            "map_coverage": _ratio(with_maps, total),
            "player_coverage": _ratio(with_players, total),
            "round_coverage": _ratio(with_rounds, total),
        }


@app.get("/api/upcoming")
def upcoming_matches(limit: int = 200, team_id: int | None = None, days: int = 14):
    limit = max(1, min(limit, 500))
    Session = session_factory()
    now = datetime.now(UTC).replace(tzinfo=None)
    date_to = now + timedelta(days=max(1, min(days, 90)))
    live_cutoff = now - timedelta(hours=24)
    with Session() as session:
        Team1 = aliased(Team)
        Team2 = aliased(Team)
        query = (
            select(Match, Event, Team1, Team2)
            .outerjoin(Event, Match.event_id == Event.id)
            .join(Team1, Match.team1_id == Team1.id)
            .join(Team2, Match.team2_id == Team2.id)
            .where(
                ((Match.status == "live") & (Match.match_time >= live_cutoff))
                | ((Match.match_time >= now) & (Match.match_time <= date_to))
            )
        )
        if team_id is not None:
            query = query.where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        rows = session.execute(query.order_by(Match.match_time.asc(), Match.id.asc()).limit(limit)).all()
        result = []
        for match, event, team1, team2 in rows:
            event_name = event.name if event else None
            result.append({
                "id": match.id,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "status": match.status,
                "event": event_name,
                "event_priority": _event_priority(event_name),
                "team1": {"id": team1.id, "name": team1.name},
                "team2": {"id": team2.id, "name": team2.name},
                "score_team1": match.score_team1,
                "score_team2": match.score_team2,
                "completeness": _match_completeness(session, match),
            })
        result.sort(
            key=lambda item: (
                -int(item["event_priority"]["priority"]),
                str(item.get("match_time") or ""),
                str(item.get("event") or ""),
            )
        )
        return result


@app.get("/api/upcoming/tournaments")
def upcoming_tournaments(days: int = 14, limit: int = 100):
    limit = max(1, min(limit, 500))
    now = datetime.now(UTC).replace(tzinfo=None)
    date_to = now + timedelta(days=max(1, min(days, 90)))
    live_cutoff = now - timedelta(hours=24)
    Session = session_factory()
    with Session() as session:
        rows = session.execute(
            select(Event.name, func.count(Match.id), func.min(Match.match_time), func.max(Match.match_time))
            .join(Match, Match.event_id == Event.id)
            .where(
                _included_event(),
                ((Match.status == "live") & (Match.match_time >= live_cutoff))
                | ((Match.match_time >= now) & (Match.match_time <= date_to)),
            )
            .group_by(Event.name)
            .limit(limit)
        ).all()
        result = []
        for name, matches_count, starts_at, ends_at in rows:
            priority = _event_priority(name)
            result.append(
                {
                    "name": name,
                    "matches": matches_count,
                    "starts_at": starts_at.isoformat() if starts_at else None,
                    "ends_at": ends_at.isoformat() if ends_at else None,
                    "tier": priority["tier"],
                    "priority": priority["priority"],
                    "label": priority["label"],
                }
            )
        return sorted(result, key=lambda row: (-int(row["priority"]), str(row["starts_at"] or ""), row["name"] or ""))


@app.get("/api/matches/{match_id}")
def match_detail(match_id: int):
    Session = session_factory()
    with Session() as session:
        match = session.get(Match, match_id)
        if match is None:
            return {"ok": False, "error": "Match not found"}
        team1 = session.get(Team, match.team1_id) if match.team1_id else None
        team2 = session.get(Team, match.team2_id) if match.team2_id else None
        event = session.get(Event, match.event_id) if match.event_id else None
        maps = session.scalars(select(MatchMap).where(MatchMap.match_id == match.id).order_by(MatchMap.map_number)).all()
        map_ids = [item.id for item in maps]
        rounds = session.scalars(
            select(Round).where(Round.match_map_id.in_(map_ids)).order_by(Round.match_map_id, Round.round_number)
        ).all() if map_ids else []
        round_status_rows = session.scalars(
            select(ExternalEntityMap).where(
                ExternalEntityMap.provider == "dust2",
                ExternalEntityMap.entity_type == "map_rounds",
                ExternalEntityMap.local_id.in_(map_ids),
            )
        ).all() if map_ids else []
        round_status_by_map = {row.local_id: row.name for row in round_status_rows}
        stats = session.execute(
            select(PlayerMapStat, Player, Team, MatchMap)
            .join(Player, PlayerMapStat.player_id == Player.id)
            .outerjoin(Team, PlayerMapStat.team_id == Team.id)
            .join(MatchMap, PlayerMapStat.match_map_id == MatchMap.id)
            .where(MatchMap.match_id == match.id)
            .order_by(MatchMap.map_number, desc(PlayerMapStat.kills))
        ).all()

        def aggregate(team_id: int | None, map_id: int | None = None) -> dict[str, Any]:
            selected = [
                stat
                for stat, _player, _team, match_map in stats
                if stat.team_id == team_id and (map_id is None or match_map.id == map_id)
            ]
            kills = sum(item.kills or 0 for item in selected)
            deaths = sum(item.deaths or 0 for item in selected)
            assists = sum(item.assists or 0 for item in selected)
            adr_values = [float(item.adr) for item in selected if item.adr is not None]
            return {
                "players": len({item.player_id for item in selected}),
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "kd_ratio": _ratio(kills, deaths),
                "avg_adr": round(sum(adr_values) / len(adr_values), 2) if adr_values else None,
            }

        def round_aggregate(team_id: int | None, map_id: int | None = None) -> dict[str, Any]:
            selected = [item for item in rounds if (map_id is None or item.match_map_id == map_id)]
            won = [item for item in selected if item.winner_team_id == team_id]
            pistols = [item for item in selected if item.is_pistol]
            return {
                "rounds_won": len(won),
                "t_rounds_won": sum(item.winner_side == "T" for item in won),
                "ct_rounds_won": sum(item.winner_side == "CT" for item in won),
                "pistol_rounds": len(pistols),
                "pistol_rounds_won": sum(item.winner_team_id == team_id for item in pistols),
                "pistol_win_rate": _ratio(sum(item.winner_team_id == team_id for item in pistols), len(pistols)),
            }

        map_payload = []
        for item in maps:
            map_stats = [
                {
                    "player": player.nickname,
                    "player_id": player.id,
                    "team": team.name if team else None,
                    "team_id": team.id if team else None,
                    "kills": stat.kills,
                    "deaths": stat.deaths,
                    "assists": stat.assists,
                    "kd_ratio": stat.kd_ratio,
                    "adr": stat.adr,
                    "headshot_percentage": stat.headshot_percentage,
                }
                for stat, player, team, match_map in stats
                if match_map.id == item.id
            ]
            map_payload.append({
                "id": item.id,
                "number": item.map_number,
                "name": item.name,
                "score_team1": item.score_team1,
                "score_team2": item.score_team2,
                "first_half_team1": item.first_half_team1,
                "first_half_team2": item.first_half_team2,
                "second_half_team1": item.second_half_team1,
                "second_half_team2": item.second_half_team2,
                "overtime": item.overtime,
                "winner_team_id": item.winner_team_id,
                "picked_by_team_id": item.picked_by_team_id,
                "team1_stats": aggregate(match.team1_id, item.id),
                "team2_stats": aggregate(match.team2_id, item.id),
                "team1_rounds": round_aggregate(match.team1_id, item.id),
                "team2_rounds": round_aggregate(match.team2_id, item.id),
                "round_status": {
                    "provider": "dust2",
                    "reason": round_status_by_map.get(item.id) or "Раундов нет: источник с полной историей пока не найден",
                    "checked": item.id in round_status_by_map,
                },
                "player_stats": map_stats,
                "round_history": [
                    {
                        "number": round_item.round_number,
                        "half": round_item.half_number,
                        "overtime": round_item.is_overtime,
                        "winner_team_id": round_item.winner_team_id,
                        "winner_side": round_item.winner_side,
                        "end_method": round_item.end_method,
                        "score_team1": round_item.score_team1_after,
                        "score_team2": round_item.score_team2_after,
                        "is_pistol": round_item.is_pistol,
                    }
                    for round_item in rounds if round_item.match_map_id == item.id
                ],
            })
        event_name = event.name if event else None
        return {
            "ok": True,
            "match": {
                "id": match.id,
                "source_url": match.source_url,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "status": match.status,
                "event": event_name,
                "event_priority": _event_priority(event_name),
                "team1": {"id": team1.id, "name": team1.name} if team1 else None,
                "team2": {"id": team2.id, "name": team2.name} if team2 else None,
                "winner_team_id": match.winner_team_id,
                "best_of": match.best_of,
                "score_team1": match.score_team1,
                "score_team2": match.score_team2,
                "completeness": _match_completeness(session, match, maps),
                "team1_stats": aggregate(match.team1_id),
                "team2_stats": aggregate(match.team2_id),
                "team1_rounds": round_aggregate(match.team1_id),
                "team2_rounds": round_aggregate(match.team2_id),
                "maps": map_payload,
                "player_stats": [
                    {
                        "player": player.nickname,
                        "player_id": player.id,
                        "team": team.name if team else None,
                        "team_id": team.id if team else None,
                        "map_id": match_map.id,
                        "map": match_map.name,
                        "kills": stat.kills,
                        "deaths": stat.deaths,
                        "assists": stat.assists,
                        "kd_ratio": stat.kd_ratio,
                        "adr": stat.adr,
                        "headshot_percentage": stat.headshot_percentage,
                    }
                    for stat, player, team, match_map in stats
                ],
            },
        }


@app.get("/api/matches/{match_id}/preview")
def match_preview(match_id: int, window: int = 20, stats_window: str = "LAST_MONTH"):
    Session = session_factory()
    with Session() as session:
        match = session.get(Match, match_id)
        if match is None:
            return {"ok": False, "error": "Match not found"}
        team1 = session.get(Team, match.team1_id) if match.team1_id else None
        team2 = session.get(Team, match.team2_id) if match.team2_id else None
        if team1 is None or team2 is None:
            return {"ok": False, "error": "Match does not have two teams"}
        event = session.get(Event, match.event_id) if match.event_id else None
        event_name = event.name if event else None
        return {
            "ok": True,
            "match": {
                "id": match.id,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "status": match.status,
                "event": event_name,
                "event_priority": _event_priority(event_name),
                "team1": {"id": team1.id, "name": team1.name},
                "team2": {"id": team2.id, "name": team2.name},
                "score_team1": match.score_team1,
                "score_team2": match.score_team2,
                "completeness": _match_completeness(session, match),
            },
            "comparison": _preview_payload(session, team1, team2, window, stats_window),
        }


@app.get("/api/maps")
def map_names():
    Session = session_factory()
    with Session() as session:
        names = session.scalars(
            select(MatchMap.name)
            .join(Match, MatchMap.match_id == Match.id)
            .outerjoin(Event, Match.event_id == Event.id)
            .where(_included_event(), MatchMap.name != "GRID Unknown")
            .distinct()
            .order_by(MatchMap.name)
        ).all()
        return [name for name in names if name]


@app.get("/api/player-stats")
def player_stats(limit: int = 100):
    limit = max(1, min(limit, 500))
    Session = session_factory()
    with Session() as session:
        rows = session.execute(
            select(PlayerMapStat, Player, Team, MatchMap, Match)
            .join(Player, PlayerMapStat.player_id == Player.id)
            .outerjoin(Team, PlayerMapStat.team_id == Team.id)
            .join(MatchMap, PlayerMapStat.match_map_id == MatchMap.id)
            .join(Match, MatchMap.match_id == Match.id)
            .order_by(desc(Match.match_time), desc(PlayerMapStat.id))
            .limit(limit)
        ).all()
        return [
            {
                "player": player.nickname,
                "team": team.name if team else None,
                "match_id": match.id,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "map": match_map.name,
                "kills": stat.kills,
                "deaths": stat.deaths,
                "assists": stat.assists,
                "kd_ratio": stat.kd_ratio,
                "adr": stat.adr,
                "headshot_percentage": stat.headshot_percentage,
            }
            for stat, player, team, match_map, match in rows
        ]


@app.get("/api/players")
def players(limit: int = 100, team_id: int | None = None):
    limit = max(1, min(limit, 500))
    Session = session_factory()
    with Session() as session:
        query = (
            select(Player, Team, func.sum(PlayerMapStat.kills), func.sum(PlayerMapStat.deaths), func.avg(PlayerMapStat.adr), func.count(PlayerMapStat.id))
            .outerjoin(Team, Player.current_team_id == Team.id)
            .outerjoin(PlayerMapStat, PlayerMapStat.player_id == Player.id)
            .group_by(Player.id, Team.id)
            .order_by(desc(func.count(PlayerMapStat.id)), Player.nickname)
            .limit(limit)
        )
        if team_id is not None:
            query = query.where(Player.current_team_id == team_id)
        rows = session.execute(query).all()
        return [
            {
                "id": player.id,
                "nickname": player.nickname,
                "team": team.name if team else None,
                "maps": maps,
                "kills": kills,
                "deaths": deaths,
                "kd_ratio": _ratio(kills, deaths),
                "avg_adr": round(float(avg_adr), 2) if avg_adr is not None else None,
            }
            for player, team, kills, deaths, avg_adr, maps in rows
        ]


@app.post("/api/sync/grid")
def sync_grid(payload: GridSyncRequest):
    return _start_grid_sync_thread(payload)


@app.get("/api/automation")
def get_automation():
    Session = session_factory()
    with Session.begin() as session:
        row = _automation_row(session)
        recent_jobs = session.scalars(select(JobRun).order_by(desc(JobRun.created_at)).limit(50)).all()
        automation_jobs = [
            item for item in recent_jobs
            if json.loads(item.request_json or "{}").get("trigger") in {"automation", "recovery"}
        ]
        active = _job_coordinator.active_job()
        return {
            "enabled": row.enabled,
            "interval_minutes": row.interval_minutes,
            "upcoming_days": row.upcoming_days,
            "results_days": row.results_days,
            "top_limit": row.top_limit,
            "max_matches": row.max_matches,
            "refresh_stats": row.refresh_stats,
            "last_started_at": row.last_started_at.isoformat() if row.last_started_at else None,
            "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
            "scheduler_running": _automation_scheduler.running,
            "worker_running": _job_coordinator.running,
            "watchdog_running": _job_coordinator.watchdog_running,
            "queue_size": _job_coordinator.queue_size,
            "active_job": active,
            "last_automation_job": serialize_job_run(automation_jobs[0]) if automation_jobs else None,
        }


@app.put("/api/automation")
def update_automation(payload: AutomationRequest):
    Session = session_factory()
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session.begin() as session:
        row = _automation_row(session)
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        row.next_run_at = now if payload.enabled else None
    return get_automation()


@app.post("/api/sync/grid/jobs")
def start_grid_sync_job(payload: GridSyncRequest):
    return _start_grid_sync_thread(payload)


@app.post("/api/sync/grid-stats")
def sync_grid_stats(payload: GridStatsRefreshRequest):
    return _start_grid_stats_thread(payload)


@app.get("/api/sync/grid/jobs/{job_id}")
def grid_sync_job_status(job_id: str):
    job = _job_coordinator.get_job(job_id)
    if job:
        return {"ok": True, "job": job}
    Session = session_factory()
    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.job_id == job_id))
        if not row:
            return {"ok": False, "error": "Job not found"}
        return {"ok": True, "job": serialize_job_run(row)}


@app.post("/api/sync/grid/jobs/{job_id}/cancel")
def cancel_grid_sync_job(job_id: str):
    return _job_coordinator.cancel(job_id)


@app.get("/api/grid/raw-summary")
def grid_raw_summary(limit: int = 20):
    limit = max(1, min(limit, 100))
    Session = session_factory()
    with Session() as session:
        rows = session.execute(
            select(GridRawSeriesState.grid_series_id, func.count(GridRawSeriesState.id), func.max(GridRawSeriesState.fetched_at))
            .group_by(GridRawSeriesState.grid_series_id)
            .order_by(desc(func.max(GridRawSeriesState.fetched_at)))
            .limit(limit)
        ).all()
        return {
            "count": session.scalar(select(func.count(GridRawSeriesState.id))) or 0,
            "with_games": session.scalar(select(func.count(GridRawSeriesState.id)).where(GridRawSeriesState.has_games.is_(True))) or 0,
            "with_maps": session.scalar(select(func.count(GridRawSeriesState.id)).where(GridRawSeriesState.has_maps.is_(True))) or 0,
            "with_players": session.scalar(select(func.count(GridRawSeriesState.id)).where(GridRawSeriesState.has_players.is_(True))) or 0,
            "latest": [
                {"grid_series_id": series_id, "snapshots": count, "last_fetched_at": fetched_at.isoformat() if fetched_at else None}
                for series_id, count, fetched_at in rows
            ],
        }


@app.get("/api/grid/entity-summary")
def grid_entity_summary():
    Session = session_factory()
    with Session() as session:
        rows = session.execute(select(GridEntityMap.entity_type, func.count(GridEntityMap.id)).group_by(GridEntityMap.entity_type)).all()
        return {entity_type: count for entity_type, count in rows}


@app.get("/api/grid/stats")
def grid_stats(entity_type: str = "team", window: str = "LAST_MONTH", limit: int = 30):
    limit = max(1, min(limit, 100))
    Session = session_factory()
    with Session() as session:
        rows = session.scalars(
            select(GridStatsSnapshot)
            .where(GridStatsSnapshot.entity_type == entity_type, GridStatsSnapshot.window_name == window)
            .order_by(desc(GridStatsSnapshot.fetched_at), GridStatsSnapshot.name)
            .limit(limit)
        ).all()
        return [
            {
                "entity_type": row.entity_type,
                "grid_id": row.grid_id,
                "local_id": row.local_id,
                "name": row.name,
                "window": row.window_name,
                "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
                "stats": json.loads(row.payload_json),
            }
            for row in rows
        ]


@app.get("/api/data-status")
def data_status():
    Session = session_factory()
    with Session() as session:
        cursor = session.scalar(select(GridSyncCursor).where(GridSyncCursor.name == "grid-main"))
        latest_match = session.scalar(
            select(func.max(Match.match_time))
            .outerjoin(Event, Match.event_id == Event.id)
            .where(Match.status == "completed", _included_event())
        )
        latest_raw = session.scalar(select(func.max(GridRawSeriesState.fetched_at)))
        latest_stats = session.scalar(select(func.max(GridStatsSnapshot.fetched_at)))
        latest_validation = sorted((Path("data/reports")).glob("validation-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        latest_validation_path = latest_validation[0] if latest_validation else None
        validation_payload = None
        if latest_validation_path:
            try:
                validation_payload = json.loads(latest_validation_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                validation_payload = None
        job_rows = session.scalars(select(JobRun).order_by(desc(JobRun.created_at)).limit(10)).all()
        return {
            "cursor": {
                "last_successful_to": cursor.last_successful_to.isoformat() if cursor and cursor.last_successful_to else None,
                "last_run_at": cursor.last_run_at.isoformat() if cursor and cursor.last_run_at else None,
                "last_result": cursor.last_result_json if cursor else None,
            },
            "latest_match_time": latest_match.isoformat() if latest_match else None,
            "latest_raw_fetch": latest_raw.isoformat() if latest_raw else None,
            "latest_stats_fetch": latest_stats.isoformat() if latest_stats else None,
            "latest_validation_report": str(latest_validation_path) if latest_validation_path else None,
            "latest_validation_at": datetime.fromtimestamp(latest_validation_path.stat().st_mtime, UTC).replace(tzinfo=None).isoformat() if latest_validation_path else None,
            "validation_status": "passed" if validation_payload and validation_payload.get("ok") else "failed" if validation_payload else "unknown",
            "validation_issue_count": sum(int(item.get("count", 0)) for item in (validation_payload or {}).get("issues", [])),
            "jobs": [serialize_job_run(row) for row in job_rows],
        }


@app.get("/api/jobs")
def jobs(limit: int = 20):
    limit = max(1, min(limit, 200))
    Session = session_factory()
    with Session() as session:
        rows = session.scalars(select(JobRun).order_by(desc(JobRun.created_at)).limit(limit)).all()
        return [serialize_job_run(row) for row in rows]


@app.get("/api/backfill/estimate")
def backfill_estimate(days: int = 30, window_days: int = 1, max_pages: int = 20, max_matches: int = 500, refresh_stats: bool = True):
    settings = get_settings()
    return estimate_backfill(
        days=days,
        window_days=window_days,
        max_pages=max_pages,
        max_matches=max_matches,
        request_limit_per_minute=settings.grid_request_limit_per_minute,
        stats_limit=settings.grid_stats_request_limit_per_minute,
        refresh_stats=refresh_stats,
    )


@app.get("/api/backfill/calendar")
def backfill_calendar(
    days: int = 90,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str = "grid-main",
    top_limit: int = 50,
    require_top_team: bool = True,
):
    now = datetime.now(UTC).replace(tzinfo=None)
    end = _naive_utc(date_to) if date_to else now
    start = _naive_utc(date_from) if date_from else end - timedelta(days=days)
    if start > end:
        start, end = end, start
    start_day = start.date()
    end_day = end.date()
    Session = session_factory()
    with Session() as session:
        latest_snapshot_id = session.scalar(
            select(RankingSnapshot.id).order_by(desc(RankingSnapshot.id)).limit(1)
        )
        requested_scope = {
            "require_top_team": require_top_team,
            "top_limit": top_limit,
            "ranking_snapshot_id": latest_snapshot_id,
        }
        rows = session.scalars(
            select(GridBackfillDay).where(
                GridBackfillDay.cursor_name == cursor,
                GridBackfillDay.day >= start_day.isoformat(),
                GridBackfillDay.day <= end_day.isoformat(),
            )
        ).all()
        by_day = {row.day: row for row in rows}
        items = []
        summary = {"complete": 0, "partial": 0, "pending": 0, "stale": 0}
        current = start_day
        while current <= end_day:
            key = current.isoformat()
            row = by_day.get(key)
            if row is None:
                item = {"day": key, "status": "pending", "pages": 0, "checked": 0, "matched_top30": 0, "saved": 0, "errors": 0}
            else:
                stored_scope = json.loads(row.result_json or "{}").get("_scope")
                status = row.status
                if status in {"complete", "skipped_complete"} and stored_scope != requested_scope:
                    status = "stale"
                item = {
                    "day": row.day,
                    "status": status,
                    "date_from": row.date_from.isoformat() if row.date_from else None,
                    "date_to": row.date_to.isoformat() if row.date_to else None,
                    "pages": row.pages,
                    "checked": row.checked,
                    "matched_top30": row.matched_top30,
                    "saved": row.saved,
                    "skipped": row.skipped,
                    "errors": row.errors,
                    "new_matches": row.new_matches,
                    "updated_matches": row.updated_matches,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "scope_matches": stored_scope == requested_scope,
                }
            state = str(item["status"])
            summary[state if state in summary else "partial"] += 1
            items.append(item)
            current += timedelta(days=1)
        return {"from": start.isoformat(), "to": end.isoformat(), "cursor": cursor, "summary": summary, "days": items}


@app.post("/api/backfill/reset")
def backfill_reset(payload: BackfillResetRequest):
    date_from = _naive_utc(payload.date_from)
    date_to = _naive_utc(payload.date_to)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    Session = session_factory()
    with Session.begin() as session:
        reset_count = reset_backfill_days(session, date_from, date_to, payload.cursor)
    return {
        "ok": True,
        "reset": reset_count,
        "from": date_from.date().isoformat(),
        "to": date_to.date().isoformat(),
        "cursor": payload.cursor,
    }


@app.get("/api/sync/cursor")
def sync_cursor(name: str = "grid-main"):
    Session = session_factory()
    with Session() as session:
        cursor = session.scalar(select(GridSyncCursor).where(GridSyncCursor.name == name))
        if cursor is None:
            return {"ok": True, "cursor": None}
        return {
            "ok": True,
            "cursor": {
                "name": cursor.name,
                "date_from": cursor.date_from.isoformat() if cursor.date_from else None,
                "date_to": cursor.date_to.isoformat() if cursor.date_to else None,
                "last_successful_to": cursor.last_successful_to.isoformat() if cursor.last_successful_to else None,
                "last_run_at": cursor.last_run_at.isoformat() if cursor.last_run_at else None,
                "last_result": cursor.last_result_json,
            },
        }

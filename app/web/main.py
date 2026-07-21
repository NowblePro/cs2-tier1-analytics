from __future__ import annotations

import threading
import uuid
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.analytics import estimate_backfill, grid_stats_summary, pre_match_edge
from app.db import get_session_factory
from app.grid import GridClient, ingest_recent_grid_series, ingest_upcoming_grid_series, run_grid_backfill, run_grid_update_since_cursor
from app.grid.client import GridApiError
from app.grid.ingest import normalize_name
from app.grid.stats import refresh_grid_stats
from app.jobs import create_job_run, run_post_sync_pipeline, serialize_job_run, update_job_run
from app.metrics import compute_metrics
from app.models import Base
from app.models.schema import Event, GridEntityMap, GridRawSeriesState, GridStatsSnapshot, GridSyncCursor, JobRun, Match, MatchMap, Player, PlayerMapStat, RankingSnapshot, RankingSnapshotTeam, Team, TeamRollingMetric
from app.validation import validate_data

app = FastAPI(title="CS2 Tier-1 Analytics")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_schema_lock = threading.Lock()
_schema_ready = False


class GridSyncRequest(BaseModel):
    mode: str = Field(default="recent", pattern="^(recent|backfill|update|upcoming)$")
    days: int = Field(default=7, ge=1, le=365)
    date_from: datetime | None = None
    date_to: datetime | None = None
    window_days: int = Field(default=1, ge=1, le=31)
    max_pages: int = Field(default=5, ge=1, le=50)
    max_matches: int = Field(default=30, ge=1, le=500)
    history_days: int = Field(default=90, ge=0, le=365)
    history_max_pages: int = Field(default=20, ge=1, le=50)
    history_max_matches: int = Field(default=200, ge=0, le=1000)
    top_limit: int = Field(default=50, ge=1, le=100)
    require_top_team: bool = True
    cursor: str = "grid-main"
    dry_run: bool = False
    post_pipeline: bool = True
    refresh_stats: bool = True
    stats_window: str = Field(default="LAST_MONTH", pattern="^(LAST_WEEK|LAST_MONTH|LAST_3_MONTHS|LAST_6_MONTHS|LAST_YEAR)$")


class GridStatsRefreshRequest(BaseModel):
    entity_type: str = Field(default="team", pattern="^(team|player)$")
    window: str = Field(default="LAST_MONTH", pattern="^(LAST_WEEK|LAST_MONTH|LAST_3_MONTHS|LAST_6_MONTHS|LAST_YEAR)$")
    limit: int = Field(default=30, ge=1, le=100)
    dry_run: bool = False


def session_factory():
    global _schema_ready
    settings = get_settings()
    Session = get_session_factory(settings)
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                Base.metadata.create_all(Session.kw["bind"])
                _schema_ready = True
    return Session


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _date_range(payload: GridSyncRequest) -> tuple[datetime, datetime]:
    now = datetime.now(UTC).replace(tzinfo=None)
    if payload.mode == "upcoming" and payload.date_from is None and payload.date_to is None:
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


def _team_recent_metrics(session, team_id: int, limit: int) -> dict[str, Any]:
    matches = session.scalars(
        select(Match)
        .where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(limit)
    ).all()
    match_ids = [match.id for match in matches]
    completed = [match for match in matches if match.status == "completed"]
    won_matches = sum(1 for match in completed if match.winner_team_id == team_id)
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
        "matches_played": len(completed),
        "match_win_rate": _ratio(won_matches, len(completed)),
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
        .order_by(desc(Match.match_time), desc(Match.id))
        .limit(limit)
    ).all()
    return [
        {
            "id": match.id,
            "match_time": match.match_time.isoformat() if match.match_time else None,
            "status": match.status,
            "event": event.name if event else None,
            "team1": team1.name,
            "team2": team2.name,
            "score_team1": match.score_team1,
            "score_team2": match.score_team2,
            "won": match.winner_team_id == team_id if match.winner_team_id else None,
        }
        for match, event, team1, team2 in rows
    ]


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


def _team_map_pool(session, team_id: int, limit: int) -> list[dict[str, Any]]:
    matches = session.scalars(
        select(Match)
        .where((Match.team1_id == team_id) | (Match.team2_id == team_id))
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


def _preview_payload(session, team1: Team, team2: Team, window: int, stats_window: str) -> dict[str, Any]:
    team1_maps = _team_map_pool(session, team1.id, window)
    team2_maps = _team_map_pool(session, team2.id, window)
    team1_payload = {
        "id": team1.id,
        "name": team1.name,
        "metrics": _team_recent_metrics(session, team1.id, window),
        "recent_matches": _team_recent_matches(session, team1.id, 5),
        "grid_stats": _team_grid_stats(session, team1.id, team1.name, stats_window),
        "players": _team_players(session, team1.id, 5),
        "map_pool": team1_maps,
    }
    team2_payload = {
        "id": team2.id,
        "name": team2.name,
        "metrics": _team_recent_metrics(session, team2.id, window),
        "recent_matches": _team_recent_matches(session, team2.id, 5),
        "grid_stats": _team_grid_stats(session, team2.id, team2.name, stats_window),
        "players": _team_players(session, team2.id, 5),
        "map_pool": team2_maps,
    }
    coverage1 = _coverage(team1_payload)
    coverage2 = _coverage(team2_payload)
    warnings = [f"{team1.name}: {item}" for item in coverage1["warnings"]] + [f"{team2.name}: {item}" for item in coverage2["warnings"]]
    return {
        "window": window,
        "stats_window": stats_window,
        "team1": team1_payload,
        "team2": team2_payload,
        "edge": pre_match_edge(team1_payload, team2_payload),
        "metrics": _comparison_metric_rows(team1_payload, team2_payload),
        "map_pool": _map_pool_comparison(team1_maps, team2_maps),
        "player_form": {"team1": team1_payload["players"], "team2": team2_payload["players"]},
        "coverage": {"team1": coverage1, "team2": coverage2, "warnings": warnings},
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


def _set_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        _jobs[job_id].update(updates)
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


def _run_grid_sync_job(job_id: str, payload: GridSyncRequest) -> None:
    _set_job(job_id, status="running", started_at=datetime.now(UTC).isoformat())
    settings = get_settings()
    client = None
    try:
        date_from, date_to = _date_range(payload)
        client = GridClient(settings)
        Session = session_factory()
        with Session.begin() as session:
            if payload.mode == "backfill":
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
                    progress=lambda item: _set_job(job_id, progress=item),
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
                )
            if not payload.dry_run:
                if payload.post_pipeline:
                    pipeline = run_post_sync_pipeline(
                        session,
                        client,
                        stats_window=payload.stats_window,
                        stats_limit=payload.top_limit,
                        refresh_stats_enabled=payload.refresh_stats,
                    )
                    result = {"sync": result, "post_pipeline": pipeline}
                else:
                    compute_metrics(session)
        _set_job(job_id, status="completed", result=result, finished_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc), finished_at=datetime.now(UTC).isoformat())
    finally:
        if client:
            client.close()


def _start_grid_sync_thread(payload: GridSyncRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
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
    thread = threading.Thread(target=_run_grid_sync_job, args=(job_id, payload), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id, "status": "queued"}


def _run_grid_stats_job(job_id: str, payload: GridStatsRefreshRequest) -> None:
    _set_job(job_id, status="running", started_at=datetime.now(UTC).isoformat())
    settings = get_settings()
    client = None
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
            )
        _set_job(job_id, status="completed", result=result, finished_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc), finished_at=datetime.now(UTC).isoformat())
    finally:
        if client:
            client.close()


def _start_grid_stats_thread(payload: GridStatsRefreshRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
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
    thread = threading.Thread(target=_run_grid_stats_job, args=(job_id, payload), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/summary")
def summary():
    Session = session_factory()
    with Session() as session:
        latest_snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.ranking_date), desc(RankingSnapshot.id)).limit(1))
        return {
            "teams": session.scalar(select(func.count(Team.id))) or 0,
            "players": session.scalar(select(func.count(Player.id))) or 0,
            "matches": session.scalar(select(func.count(Match.id))) or 0,
            "maps": session.scalar(select(func.count(MatchMap.id))) or 0,
            "player_stats": session.scalar(select(func.count(PlayerMapStat.id))) or 0,
            "grid_raw_snapshots": session.scalar(select(func.count(GridRawSeriesState.id))) or 0,
            "grid_entity_maps": session.scalar(select(func.count(GridEntityMap.id))) or 0,
            "grid_stats_snapshots": session.scalar(select(func.count(GridStatsSnapshot.id))) or 0,
            "latest_ranking_date": latest_snapshot.ranking_date.isoformat() if latest_snapshot else None,
        }


@app.get("/api/teams")
def teams(limit: int = 30):
    Session = session_factory()
    with Session() as session:
        latest_snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.ranking_date), desc(RankingSnapshot.id)).limit(1))
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
        return [
            {
                "id": team.id,
                "name": team.name,
                "rank": rank.rank,
                "points": rank.points,
                "match_win_rate": metric.match_win_rate if metric else None,
                "map_win_rate": metric.map_win_rate if metric else None,
                "kd_ratio": metric.kd_ratio if metric else None,
                "pistol_win_rate": metric.pistol_win_rate if metric else None,
            }
            for team, rank, metric in ranking_rows
        ]


@app.get("/api/teams/{team_id}")
def team_detail(team_id: int, window: int = 20, stats_window: str = "LAST_MONTH"):
    Session = session_factory()
    with Session() as session:
        team = session.get(Team, team_id)
        if team is None:
            return {"ok": False, "error": "Team not found"}
        metric = session.scalar(select(TeamRollingMetric).where(TeamRollingMetric.team_id == team.id, TeamRollingMetric.window_name == "all"))
        grid_stats = _team_grid_stats(session, team.id, team.name, stats_window)
        return {
            "ok": True,
            "team": {
                "id": team.id,
                "name": team.name,
                "country": team.country,
                "logo_url": team.logo_url,
                "metric": {
                    "matches_played": metric.matches_played if metric else 0,
                    "match_win_rate": metric.match_win_rate if metric else None,
                    "map_win_rate": metric.map_win_rate if metric else None,
                    "kd_ratio": metric.kd_ratio if metric else None,
                    "t_round_win_rate": metric.t_round_win_rate if metric else None,
                    "ct_round_win_rate": metric.ct_round_win_rate if metric else None,
                    "pistol_win_rate": metric.pistol_win_rate if metric else None,
                },
                "recent": _team_recent_metrics(session, team.id, window),
                "recent_matches": _team_recent_matches(session, team.id, 10),
                "players": _team_players(session, team.id, 10),
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
    Session = session_factory()
    with Session() as session:
        team = session.get(Team, team_id)
        if team is None:
            return {"ok": False, "error": "Team not found"}
        return {"ok": True, "team": {"id": team.id, "name": team.name}, "players": _team_players(session, team.id, limit)}


@app.get("/api/matches")
def matches(limit: int = 50, team_id: int | None = None, map_name: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None):
    Session = session_factory()
    with Session() as session:
        Team1 = aliased(Team)
        Team2 = aliased(Team)
        query = (
            select(Match, Event, Team1, Team2)
            .outerjoin(Event, Match.event_id == Event.id)
            .join(Team1, Match.team1_id == Team1.id)
            .join(Team2, Match.team2_id == Team2.id)
        )
        if team_id is not None:
            query = query.where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        if date_from is not None:
            query = query.where(Match.match_time >= _naive_utc(date_from))
        if date_to is not None:
            query = query.where(Match.match_time <= _naive_utc(date_to))
        if map_name:
            query = query.join(MatchMap, MatchMap.match_id == Match.id).where(MatchMap.name == map_name)
        rows = session.execute(query.order_by(desc(Match.match_time), desc(Match.id)).limit(limit)).all()
        result = []
        for match, event, team1, team2 in rows:
            maps = session.scalars(select(MatchMap).where(MatchMap.match_id == match.id).order_by(MatchMap.map_number)).all()
            result.append(
                {
                    "id": match.id,
                    "source_url": match.source_url,
                    "match_time": match.match_time.isoformat() if match.match_time else None,
                    "status": match.status,
                    "event": event.name if event else None,
                    "team1": team1.name,
                    "team2": team2.name,
                    "score_team1": match.score_team1,
                    "score_team2": match.score_team2,
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
        return result


@app.get("/api/upcoming")
def upcoming_matches(limit: int = 50, team_id: int | None = None):
    Session = session_factory()
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session() as session:
        Team1 = aliased(Team)
        Team2 = aliased(Team)
        query = (
            select(Match, Event, Team1, Team2)
            .outerjoin(Event, Match.event_id == Event.id)
            .join(Team1, Match.team1_id == Team1.id)
            .join(Team2, Match.team2_id == Team2.id)
            .where((Match.status.in_(["scheduled", "live"])) | (Match.match_time >= now))
        )
        if team_id is not None:
            query = query.where((Match.team1_id == team_id) | (Match.team2_id == team_id))
        rows = session.execute(query.order_by(Match.match_time.asc(), Match.id.asc()).limit(limit)).all()
        return [
            {
                "id": match.id,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "status": match.status,
                "event": event.name if event else None,
                "team1": team1.name,
                "team2": team2.name,
                "score_team1": match.score_team1,
                "score_team2": match.score_team2,
            }
            for match, event, team1, team2 in rows
        ]


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
        stats = session.execute(
            select(PlayerMapStat, Player, Team, MatchMap)
            .join(Player, PlayerMapStat.player_id == Player.id)
            .outerjoin(Team, PlayerMapStat.team_id == Team.id)
            .join(MatchMap, PlayerMapStat.match_map_id == MatchMap.id)
            .where(MatchMap.match_id == match.id)
            .order_by(MatchMap.map_number, desc(PlayerMapStat.kills))
        ).all()
        return {
            "ok": True,
            "match": {
                "id": match.id,
                "source_url": match.source_url,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "status": match.status,
                "event": event.name if event else None,
                "team1": team1.name if team1 else None,
                "team2": team2.name if team2 else None,
                "score_team1": match.score_team1,
                "score_team2": match.score_team2,
                "maps": [
                    {"id": item.id, "number": item.map_number, "name": item.name, "score_team1": item.score_team1, "score_team2": item.score_team2}
                    for item in maps
                ],
                "player_stats": [
                    {
                        "player": player.nickname,
                        "team": team.name if team else None,
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
        return {
            "ok": True,
            "match": {
                "id": match.id,
                "match_time": match.match_time.isoformat() if match.match_time else None,
                "status": match.status,
                "event": event.name if event else None,
                "team1": {"id": team1.id, "name": team1.name},
                "team2": {"id": team2.id, "name": team2.name},
                "score_team1": match.score_team1,
                "score_team2": match.score_team2,
            },
            "comparison": _preview_payload(session, team1, team2, window, stats_window),
        }


@app.get("/api/maps")
def map_names():
    Session = session_factory()
    with Session() as session:
        names = session.scalars(select(MatchMap.name).distinct().order_by(MatchMap.name)).all()
        return [name for name in names if name]


@app.get("/api/player-stats")
def player_stats(limit: int = 100):
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


@app.post("/api/sync/grid/jobs")
def start_grid_sync_job(payload: GridSyncRequest):
    return _start_grid_sync_thread(payload)


@app.post("/api/sync/grid-stats")
def sync_grid_stats(payload: GridStatsRefreshRequest):
    return _start_grid_stats_thread(payload)


@app.get("/api/sync/grid/jobs/{job_id}")
def grid_sync_job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job:
        return {"ok": True, "job": job}
    Session = session_factory()
    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.job_id == job_id))
        if not row:
            return {"ok": False, "error": "Job not found"}
        return {"ok": True, "job": serialize_job_run(row)}


@app.get("/api/grid/raw-summary")
def grid_raw_summary(limit: int = 20):
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
        latest_match = session.scalar(select(func.max(Match.match_time)))
        latest_raw = session.scalar(select(func.max(GridRawSeriesState.fetched_at)))
        latest_stats = session.scalar(select(func.max(GridStatsSnapshot.fetched_at)))
        latest_validation = sorted((Path("data/reports")).glob("validation-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
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
            "latest_validation_report": str(latest_validation[0]) if latest_validation else None,
            "jobs": [serialize_job_run(row) for row in job_rows],
        }


@app.get("/api/jobs")
def jobs(limit: int = 20):
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


@app.post("/api/metrics/compute")
def compute_metrics_endpoint():
    Session = session_factory()
    with Session.begin() as session:
        count = compute_metrics(session)
    return {"ok": True, "teams": count}


@app.get("/api/validate")
def validate_endpoint():
    Session = session_factory()
    with Session() as session:
        return validate_data(session)

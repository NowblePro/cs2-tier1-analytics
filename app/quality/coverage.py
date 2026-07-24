from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.grid.ingest import KNOWN_MAPS, normalize_map_name
from app.models.schema import Event, Match, MatchMap, PlayerMapStat, Round, Team


def normalize_saved_map_names(session: Session, *, dry_run: bool = False) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    for match_map in session.scalars(select(MatchMap)).all():
        normalized = normalize_map_name(match_map.name)
        if normalized == match_map.name:
            continue
        changed.append(
            {
                "map_id": match_map.id,
                "match_id": match_map.match_id,
                "from": match_map.name,
                "to": normalized,
            }
        )
        if not dry_run:
            match_map.name = normalized
    return {"changed": len(changed), "items": changed[:100], "dry_run": dry_run}


def period_quality_report(
    session: Session,
    date_from: datetime,
    date_to: datetime,
    *,
    team_id: int | None = None,
    candidate_limit: int = 100,
) -> dict[str, Any]:
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    Team1 = Team.__table__.alias("quality_team1")
    Team2 = Team.__table__.alias("quality_team2")
    query = (
        select(
            Match,
            Event.name,
            Team1.c.name,
            Team2.c.name,
        )
        .outerjoin(Event, Match.event_id == Event.id)
        .outerjoin(Team1, Match.team1_id == Team1.c.id)
        .outerjoin(Team2, Match.team2_id == Team2.c.id)
        .where(
            Match.match_time >= date_from,
            Match.match_time <= date_to,
            Match.status == "completed",
        )
        .order_by(Match.match_time.desc(), Match.id.desc())
    )
    if team_id is not None:
        query = query.where((Match.team1_id == team_id) | (Match.team2_id == team_id))
    match_rows = session.execute(query).all()
    match_ids = [match.id for match, _event, _team1, _team2 in match_rows]

    maps = (
        session.scalars(
            select(MatchMap)
            .where(MatchMap.match_id.in_(match_ids))
            .order_by(MatchMap.match_id, MatchMap.map_number)
        ).all()
        if match_ids
        else []
    )
    maps_by_match: dict[int, list[MatchMap]] = defaultdict(list)
    for match_map in maps:
        maps_by_match[match_map.match_id].append(match_map)
    map_ids = [match_map.id for match_map in maps]
    player_counts = dict(
        session.execute(
            select(PlayerMapStat.match_map_id, func.count(PlayerMapStat.id))
            .where(PlayerMapStat.match_map_id.in_(map_ids))
            .group_by(PlayerMapStat.match_map_id)
        ).all()
    ) if map_ids else {}
    round_counts = dict(
        session.execute(
            select(Round.match_map_id, func.count(Round.id))
            .where(Round.match_map_id.in_(map_ids))
            .group_by(Round.match_map_id)
        ).all()
    ) if map_ids else {}

    levels = {"invalid": 0, "result": 0, "maps": 0, "players": 0, "rounds": 0}
    days: dict[str, dict[str, int]] = defaultdict(
        lambda: {"matches": 0, "invalid": 0, "result": 0, "maps": 0, "players": 0, "rounds": 0}
    )
    candidates: list[dict[str, Any]] = []
    repairable_total = 0

    for match, event_name, team1_name, team2_name in match_rows:
        match_maps = maps_by_match.get(match.id, [])
        reasons: list[str] = []
        if match.team1_id is None or match.team2_id is None:
            reasons.append("missing_teams")
        if match.winner_team_id is None:
            reasons.append("missing_winner")
        if match.score_team1 is None or match.score_team2 is None:
            reasons.append("missing_series_score")
        if not match_maps:
            reasons.append("missing_maps")
        elif any(item.score_team1 is None or item.score_team2 is None for item in match_maps):
            reasons.append("map_without_score")
        if any(item.name not in KNOWN_MAPS or item.name == "GRID Unknown" for item in match_maps):
            reasons.append("unknown_map")

        player_count = sum(int(player_counts.get(item.id, 0)) for item in match_maps)
        round_count = sum(int(round_counts.get(item.id, 0)) for item in match_maps)
        if not match_maps:
            level = "result"
        elif not player_count:
            level = "maps"
        elif not round_count:
            level = "players"
        else:
            level = "rounds"
        invalid = any(
            reason in {"missing_teams", "missing_winner", "missing_series_score", "map_without_score"}
            for reason in reasons
        )
        bucket = "invalid" if invalid else level
        levels[bucket] += 1
        day = match.match_time.date().isoformat() if match.match_time else "unknown"
        days[day]["matches"] += 1
        days[day][bucket] += 1

        repairable = bool(match.source_url and match.source_url.startswith("grid://series/"))
        needs_repair = invalid or level in {"result", "maps"} or "unknown_map" in reasons
        if repairable and needs_repair:
            repairable_total += 1
        if repairable and needs_repair and len(candidates) < candidate_limit:
            candidates.append(
                {
                    "match_id": match.id,
                    "source_url": match.source_url,
                    "match_time": match.match_time.isoformat() if match.match_time else None,
                    "event": event_name,
                    "team1": team1_name,
                    "team2": team2_name,
                    "level": level,
                    "reasons": reasons,
                    "maps": len(match_maps),
                    "player_stats": player_count,
                    "rounds": round_count,
                }
            )

    total = len(match_rows)
    detailed = levels["maps"] + levels["players"] + levels["rounds"]
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "team_id": team_id,
        "matches": total,
        "levels": levels,
        "valid_matches": total - levels["invalid"],
        "map_coverage": round(detailed / total, 4) if total else 1.0,
        "player_coverage": round((levels["players"] + levels["rounds"]) / total, 4) if total else 1.0,
        "round_coverage": round(levels["rounds"] / total, 4) if total else 1.0,
        "repairable_count": repairable_total,
        "candidate_count": len(candidates),
        "repair_candidates": candidates,
        "days": [{"day": day, **values} for day, values in sorted(days.items())],
    }

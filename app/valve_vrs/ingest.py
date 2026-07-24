from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.grid.ids import stable_negative_id
from app.models.schema import RankingSnapshot, RankingSnapshotTeam, Team
from app.repositories.team_aliases import canonical_team_key, find_team_by_alias
from app.valve_vrs.client import ValveVrsClient
from app.valve_vrs.parser import parse_valve_ranking


def ingest_latest_valve_ranking(
    session: Session,
    client: ValveVrsClient,
    *,
    limit: int = 100,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    markdown, source_url = client.fetch_latest_global()
    ranking = parse_valve_ranking(markdown, limit=limit)
    existing = session.scalar(select(RankingSnapshot).where(RankingSnapshot.source_url == source_url))
    if existing:
        return {
            "provider": "valve-vrs",
            "ranking_date": ranking.ranking_date.date().isoformat(),
            "teams": len(ranking.teams),
            "created": False,
            "snapshot_id": existing.id,
            "source_url": source_url,
        }
    if dry_run:
        return {
            "provider": "valve-vrs",
            "ranking_date": ranking.ranking_date.date().isoformat(),
            "teams": len(ranking.teams),
            "created": False,
            "dry_run": True,
            "source_url": source_url,
        }
    snapshot = RankingSnapshot(ranking_date=ranking.ranking_date, source_url=source_url)
    session.add(snapshot)
    session.flush()
    for index, ranked in enumerate(ranking.teams, start=1):
        team = find_team_by_alias(session, ranked.name)
        if team is None:
            team = Team(hltv_team_id=stable_negative_id(f"valve-vrs-team:{canonical_team_key(ranked.name)}"), name=ranked.name)
            session.add(team)
            session.flush()
        session.add(RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=team.id, rank=ranked.rank, points=ranked.points))
        if progress and (index == 1 or index % 10 == 0 or index == len(ranking.teams)):
            progress({"stage": "valve-ranking", "processed": index, "total": len(ranking.teams), "ranking_date": ranking.ranking_date.date().isoformat()})
    return {
        "provider": "valve-vrs",
        "ranking_date": ranking.ranking_date.date().isoformat(),
        "teams": len(ranking.teams),
        "created": True,
        "snapshot_id": snapshot.id,
        "source_url": source_url,
    }

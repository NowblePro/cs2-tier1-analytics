from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import ExternalEntityMap, RankingSnapshot, RankingSnapshotTeam, Team
from app.repositories.team_aliases import canonical_team_key


def _latest_ranked_teams(session: Session, limit: int) -> tuple[RankingSnapshot | None, list[tuple[Team, int]]]:
    snapshot = session.scalar(select(RankingSnapshot).order_by(desc(RankingSnapshot.id)).limit(1))
    if snapshot is None:
        return None, []
    rows = session.execute(
        select(Team, RankingSnapshotTeam.rank)
        .join(RankingSnapshotTeam, RankingSnapshotTeam.team_id == Team.id)
        .where(RankingSnapshotTeam.snapshot_id == snapshot.id)
        .order_by(RankingSnapshotTeam.rank)
        .limit(limit)
    ).all()
    return snapshot, [(team, rank) for team, rank in rows]


def _provider_names(session: Session, team_id: int, provider: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == provider,
            ExternalEntityMap.entity_type == "team",
            ExternalEntityMap.local_id == team_id,
        )
    ).all()
    return [
        {"external_id": row.external_id, "name": row.name, "canonical_key": canonical_team_key(row.name)}
        for row in rows
    ]


def _save_pandascore_mapping(session: Session, team: Team, candidate: dict[str, Any]) -> bool:
    external_id = str(candidate.get("id", ""))
    if not external_id:
        return False
    existing = session.scalar(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == "pandascore",
            ExternalEntityMap.entity_type == "team",
            ExternalEntityMap.external_id == external_id,
        )
    )
    if existing is not None:
        if existing.local_id != team.id:
            return False
        existing.name = candidate.get("name")
        return True
    session.add(
        ExternalEntityMap(
            provider="pandascore",
            entity_type="team",
            external_id=external_id,
            local_table="teams",
            local_id=team.id,
            name=candidate.get("name"),
        )
    )
    return True


def audit_top_team_aliases(
    session: Session,
    *,
    limit: int = 50,
    pandascore_client: Any | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    snapshot, ranked_teams = _latest_ranked_teams(session, min(max(limit, 1), 100))
    if snapshot is None:
        raise RuntimeError("No ranking snapshot found. Load a ranking first.")

    results: list[dict[str, Any]] = []
    saved = 0
    for team, rank in ranked_teams:
        key = canonical_team_key(team.name)
        row: dict[str, Any] = {
            "rank": rank,
            "local_team_id": team.id,
            "ranking_name": team.name,
            "canonical_key": key,
            "providers": {
                "pandascore": _provider_names(session, team.id, "pandascore"),
                "dust2": _provider_names(session, team.id, "dust2"),
            },
            "pandascore_candidates": [],
            "status": "unverified",
        }
        if pandascore_client is not None:
            candidates = pandascore_client.search_teams(team.name)
            row["pandascore_candidates"] = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "canonical_key": canonical_team_key(item.get("name")),
                    "match": canonical_team_key(item.get("name")) == key,
                }
                for item in candidates
            ]
            exact = [item for item in candidates if canonical_team_key(item.get("name")) == key]
            if len(exact) == 1:
                row["status"] = "matched"
                if not dry_run and _save_pandascore_mapping(session, team, exact[0]):
                    saved += 1
            elif len(exact) > 1:
                row["status"] = "ambiguous"
            else:
                row["status"] = "not_found"
        elif row["providers"]["pandascore"] or row["providers"]["dust2"]:
            row["status"] = "known_locally"
        results.append(row)

    return {
        "snapshot_id": snapshot.id,
        "ranking_date": snapshot.ranking_date.isoformat(),
        "limit": limit,
        "checked": len(results),
        "matched": sum(item["status"] == "matched" for item in results),
        "ambiguous": sum(item["status"] == "ambiguous" for item in results),
        "not_found": sum(item["status"] == "not_found" for item in results),
        "known_locally": sum(item["status"] == "known_locally" for item in results),
        "saved_mappings": saved,
        "dry_run": dry_run,
        "teams": results,
    }


def save_alias_audit(report: dict[str, Any], directory: str | Path = "data/reports") -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"team-alias-audit-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

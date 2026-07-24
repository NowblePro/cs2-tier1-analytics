from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.dust2.client import Dust2Client, Dust2FetchError
from app.dust2.importer import import_dust2_match
from app.dust2.resolver import resolve_dust2_match
from app.models.schema import ExternalEntityMap, Match, MatchMap, Round


def sync_missing_dust2_rounds(
    session: Session,
    client: Dust2Client,
    *,
    match_ids: list[int] | None = None,
    limit: int = 25,
    min_score: int = 70,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    matches = _candidate_matches(session, match_ids, limit)
    checked = imported = skipped = errors = rounds_imported = no_dust2_match = 0
    cancelled = False
    for index, match in enumerate(matches, start=1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        checked += 1
        if progress:
            progress(
                {
                    "stage": "rounds",
                    "provider": "dust2",
                    "current": index,
                    "total": len(matches),
                    "match_id": match.id,
                    "checked": checked,
                    "imported": imported,
                    "skipped": skipped,
                    "errors": errors,
                    "progress_percent": round((index - 1) / max(len(matches), 1) * 100, 2),
                }
            )
        try:
            candidates = resolve_dust2_match(session, client, match.id)
        except Dust2FetchError as exc:
            _mark_match_maps(session, match.id, f"Раундов нет: Dust2 сейчас недоступен ({exc})")
            errors += 1
            skipped += 1
            continue
        if not candidates or candidates[0].score < min_score:
            _mark_match_maps(session, match.id, "Раундов нет: матч не найден на Dust2")
            no_dust2_match += 1
            skipped += 1
            continue
        try:
            html = client.fetch_match(candidates[0].url)
            result = import_dust2_match(session, html, match_id=match.id, url=candidates[0].url)
        except Dust2FetchError as exc:
            _mark_match_maps(session, match.id, f"Раундов нет: Dust2 сейчас недоступен ({exc})")
            errors += 1
            skipped += 1
            continue
        imported += 1
        rounds_imported += result.rounds_imported
    if progress:
        progress(
            {
                "stage": "rounds",
                "provider": "dust2",
                "current": checked,
                "total": len(matches),
                "checked": checked,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "rounds_imported": rounds_imported,
                "progress_percent": 100,
            }
        )
    return {
        "checked": checked,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "no_dust2_match": no_dust2_match,
        "rounds_imported": rounds_imported,
        "cancelled": cancelled,
    }


def _candidate_matches(session: Session, match_ids: list[int] | None, limit: int) -> list[Match]:
    query = (
        select(Match)
        .where(
            Match.status == "completed",
            exists(select(MatchMap.id).where(MatchMap.match_id == Match.id)),
        )
        .order_by(Match.match_time.desc(), Match.id.desc())
    )
    if match_ids:
        query = query.where(Match.id.in_(match_ids))
    else:
        query = query.where(
            ~exists(
                select(Round.id)
                .join(MatchMap, Round.match_map_id == MatchMap.id)
                .where(MatchMap.match_id == Match.id)
            )
        ).limit(max(1, limit))
    return list(session.scalars(query))


def _mark_match_maps(session: Session, match_id: int, reason: str) -> None:
    maps = session.scalars(select(MatchMap).where(MatchMap.match_id == match_id)).all()
    for match_map in maps:
        _save_round_status(session, match_map, reason)


def _save_round_status(session: Session, match_map: MatchMap, reason: str) -> None:
    external_id = f"match_map:{match_map.id}"
    row = session.scalar(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == "dust2",
            ExternalEntityMap.entity_type == "map_rounds",
            ExternalEntityMap.external_id == external_id,
        )
    )
    if row is None:
        row = ExternalEntityMap(
            provider="dust2",
            entity_type="map_rounds",
            external_id=external_id,
            local_table="match_maps",
            local_id=match_map.id,
        )
        session.add(row)
    row.local_id = match_map.id
    row.name = reason

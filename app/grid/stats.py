from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.grid.client import GridApiError, GridClient
from app.models.schema import GridEntityMap, GridStatsSnapshot

logger = logging.getLogger(__name__)

VALID_WINDOWS = {"LAST_WEEK", "LAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS", "LAST_YEAR"}


def _is_api_grid_id(value: str) -> bool:
    return value.isdigit()


def _is_steam64_id(value: str) -> bool:
    return value.startswith("7656119") and len(value) == 17 and value.isdigit()


def _upsert_stats_snapshot(
    session: Session,
    entity: GridEntityMap,
    window_name: str,
    payload: dict[str, Any],
) -> GridStatsSnapshot:
    row = session.scalar(
        select(GridStatsSnapshot).where(
            GridStatsSnapshot.entity_type == entity.entity_type,
            GridStatsSnapshot.grid_id == entity.grid_id,
            GridStatsSnapshot.window_name == window_name,
        )
    )
    if row is None:
        row = GridStatsSnapshot(entity_type=entity.entity_type, grid_id=entity.grid_id, window_name=window_name)
        session.add(row)
    row.local_table = entity.local_table
    row.local_id = entity.local_id
    row.name = entity.name
    row.payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    row.fetched_at = datetime.now(UTC).replace(tzinfo=None)
    return row


def refresh_grid_stats(
    session: Session,
    client: GridClient,
    *,
    entity_type: str = "team",
    window_name: str = "LAST_MONTH",
    limit: int = 30,
    dry_run: bool = False,
) -> dict[str, int]:
    if entity_type not in {"team", "player"}:
        raise ValueError("entity_type must be 'team' or 'player'")
    if window_name not in VALID_WINDOWS:
        raise ValueError(f"window_name must be one of: {', '.join(sorted(VALID_WINDOWS))}")

    entities = session.scalars(
        select(GridEntityMap)
        .where(GridEntityMap.entity_type == entity_type)
        .order_by(GridEntityMap.local_id.is_(None), GridEntityMap.name, GridEntityMap.grid_id)
    ).all()
    unique_entities: list[GridEntityMap] = []
    seen: set[str] = set()
    for entity in entities:
        if entity.grid_id in seen or not _is_api_grid_id(entity.grid_id):
            continue
        seen.add(entity.grid_id)
        unique_entities.append(entity)
        if len(unique_entities) >= limit:
            break

    saved = skipped = errors = 0
    for entity in unique_entities:
        if dry_run:
            skipped += 1
            continue
        if entity_type == "player" and _is_steam64_id(entity.grid_id):
            logger.warning(
                "Skipping GRID player stats %s: seriesState exposes Steam64 ids, but Stats Feed playerStatistics expects GRID player ids",
                entity.grid_id,
            )
            skipped += 1
            continue
        try:
            if entity_type == "team":
                payload = client.team_statistics(entity.grid_id, window_name)
            else:
                payload = client.player_statistics(entity.grid_id, window_name)
            _upsert_stats_snapshot(session, entity, window_name, payload)
            saved += 1
        except GridApiError as exc:
            logger.warning("Skipping GRID %s stats %s: %s", entity_type, entity.grid_id, exc)
            errors += 1
    return {
        "checked": len(unique_entities),
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
    }

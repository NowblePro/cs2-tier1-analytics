from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.grid.client import GridClient
from app.grid.ingest import ingest_recent_grid_series
from app.models.schema import GridSyncCursor


ProgressCallback = Callable[[dict[str, object]], None]


@dataclass
class BackfillTotals:
    windows: int = 0
    pages: int = 0
    checked: int = 0
    matched_top30: int = 0
    saved: int = 0
    skipped: int = 0
    errors: int = 0
    new_matches: int = 0
    updated_matches: int = 0
    window_results: list[dict[str, object]] = field(default_factory=list)

    def add(self, window_from: datetime, window_to: datetime, result: dict[str, int]) -> None:
        self.windows += 1
        for key in ["pages", "checked", "matched_top30", "saved", "skipped", "errors", "new_matches", "updated_matches"]:
            setattr(self, key, getattr(self, key) + int(result.get(key, 0)))
        self.window_results.append(
            {
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "result": result,
            }
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "windows": self.windows,
            "pages": self.pages,
            "checked": self.checked,
            "matched_top30": self.matched_top30,
            "saved": self.saved,
            "skipped": self.skipped,
            "errors": self.errors,
            "new_matches": self.new_matches,
            "updated_matches": self.updated_matches,
            "window_results": self.window_results,
        }


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def get_or_create_cursor(session: Session, name: str) -> GridSyncCursor:
    cursor = session.scalar(select(GridSyncCursor).where(GridSyncCursor.name == name))
    if cursor is None:
        cursor = GridSyncCursor(name=name)
        session.add(cursor)
        session.flush()
    return cursor


def default_backfill_range(days: int = 30) -> tuple[datetime, datetime]:
    date_to = utcnow_naive()
    return date_to - timedelta(days=days), date_to


def run_grid_backfill(
    session: Session,
    client: GridClient,
    date_from: datetime,
    date_to: datetime,
    *,
    cursor_name: str = "grid-main",
    window_days: int = 1,
    max_pages_per_window: int = 20,
    max_matches_per_window: int = 500,
    top_limit: int = 50,
    require_top_team: bool = True,
    dry_run: bool = False,
    resume: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    if date_from >= date_to:
        raise ValueError("date_from must be earlier than date_to")
    if window_days < 1:
        raise ValueError("window_days must be >= 1")

    cursor = get_or_create_cursor(session, cursor_name)
    original_from = date_from
    if resume and cursor.last_successful_to and cursor.last_successful_to > date_from:
        date_from = min(cursor.last_successful_to, date_to)

    totals = BackfillTotals()
    current = date_from
    while current < date_to:
        window_to = min(current + timedelta(days=window_days), date_to)
        remaining_matches = max(0, max_matches_per_window - totals.saved)
        if remaining_matches == 0:
            break
        result = ingest_recent_grid_series(
            session=session,
            client=client,
            date_from=current,
            date_to=window_to,
            max_pages=max_pages_per_window,
            max_matches=remaining_matches,
            dry_run=dry_run,
            top_limit=top_limit,
            require_top_team=require_top_team,
        )
        totals.add(current, window_to, result)
        if not dry_run:
            cursor.date_from = original_from
            cursor.date_to = date_to
            cursor.last_successful_to = window_to
            cursor.last_run_at = utcnow_naive()
            cursor.last_result_json = json.dumps(totals.as_dict(), ensure_ascii=False)
            session.flush()
        if progress:
            progress({"window_from": current.isoformat(), "window_to": window_to.isoformat(), "totals": totals.as_dict()})
        if totals.saved >= max_matches_per_window:
            break
        current = window_to
    return totals.as_dict()


def run_grid_update_since_cursor(
    session: Session,
    client: GridClient,
    *,
    cursor_name: str = "grid-main",
    fallback_days: int = 7,
    max_pages: int = 20,
    max_matches: int = 500,
    top_limit: int = 50,
    require_top_team: bool = True,
    dry_run: bool = False,
) -> dict[str, object]:
    cursor = get_or_create_cursor(session, cursor_name)
    date_to = utcnow_naive()
    date_from = cursor.last_successful_to or (date_to - timedelta(days=fallback_days))
    result = ingest_recent_grid_series(
        session=session,
        client=client,
        date_from=date_from,
        date_to=date_to,
        max_pages=max_pages,
        max_matches=max_matches,
        dry_run=dry_run,
        top_limit=top_limit,
        require_top_team=require_top_team,
    )
    if not dry_run:
        cursor.date_from = cursor.date_from or date_from
        cursor.date_to = date_to
        cursor.last_successful_to = date_to
        cursor.last_run_at = utcnow_naive()
        cursor.last_result_json = json.dumps(result, ensure_ascii=False)
        session.flush()
    return {"from": date_from.isoformat(), "to": date_to.isoformat(), "result": result}

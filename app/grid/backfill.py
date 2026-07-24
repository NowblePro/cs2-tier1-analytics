from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.grid.client import GridClient
from app.grid.ingest import ingest_recent_grid_series
from app.models.schema import GridBackfillDay, GridSyncCursor, RankingSnapshot


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

    def add(self, window_from: datetime, window_to: datetime, result: dict[str, int], *, status: str = "complete") -> None:
        self.windows += 1
        for key in ["pages", "checked", "matched_top30", "saved", "skipped", "errors", "new_matches", "updated_matches"]:
            setattr(self, key, getattr(self, key) + int(result.get(key, 0)))
        self.window_results.append(
            {
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "status": status,
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


def reset_backfill_days(session: Session, date_from: datetime, date_to: datetime, cursor_name: str = "grid-main") -> int:
    rows = session.scalars(
        select(GridBackfillDay).where(
            GridBackfillDay.cursor_name == cursor_name,
            GridBackfillDay.day >= date_from.date().isoformat(),
            GridBackfillDay.day <= date_to.date().isoformat(),
        )
    ).all()
    for row in rows:
        row.status = "pending"
        row.completed_at = None
    return len(rows)


def _day_key(value: datetime) -> str:
    return value.date().isoformat()


def _window_count(date_from: datetime, date_to: datetime, window_days: int) -> int:
    seconds = max(0.0, (date_to - date_from).total_seconds())
    return max(1, math.ceil(seconds / (window_days * 24 * 60 * 60)))


def _completed_day(session: Session, cursor_name: str, window_from: datetime, window_to: datetime, scope: dict[str, object]) -> GridBackfillDay | None:
    row = session.scalar(
        select(GridBackfillDay).where(
            GridBackfillDay.cursor_name == cursor_name,
            GridBackfillDay.day == _day_key(window_from),
        )
    )
    if row is None:
        return None
    if row.status != "complete" or row.errors != 0:
        return None
    if row.date_from > window_from or row.date_to < window_to:
        return None
    try:
        stored_scope = json.loads(row.result_json or "{}").get("_scope")
    except json.JSONDecodeError:
        return None
    if stored_scope != scope:
        return None
    return row


def _save_day_result(session: Session, cursor_name: str, window_from: datetime, window_to: datetime, result: dict[str, object], status: str, scope: dict[str, object]) -> None:
    row = session.scalar(
        select(GridBackfillDay).where(
            GridBackfillDay.cursor_name == cursor_name,
            GridBackfillDay.day == _day_key(window_from),
        )
    )
    if row is None:
        row = GridBackfillDay(cursor_name=cursor_name, day=_day_key(window_from), date_from=window_from, date_to=window_to)
        session.add(row)
    row.date_from = window_from
    row.date_to = window_to
    row.status = status
    row.pages = int(result.get("pages", 0))
    row.checked = int(result.get("checked", 0))
    row.matched_top30 = int(result.get("matched_top30", 0))
    row.saved = int(result.get("saved", 0))
    row.skipped = int(result.get("skipped", 0))
    row.errors = int(result.get("errors", 0))
    row.new_matches = int(result.get("new_matches", 0))
    row.updated_matches = int(result.get("updated_matches", 0))
    row.result_json = json.dumps({**result, "_scope": scope}, ensure_ascii=False, sort_keys=True)
    row.completed_at = utcnow_naive() if status == "complete" else None


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
    resume: bool = False,
    progress: ProgressCallback | None = None,
    checkpoint: Callable[[], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
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
    started = time.monotonic()
    total_windows = _window_count(date_from, date_to, window_days)
    latest_snapshot_id = session.scalar(select(RankingSnapshot.id).order_by(RankingSnapshot.id.desc()).limit(1)) if require_top_team else None
    scope = {"require_top_team": require_top_team, "top_limit": top_limit if require_top_team else None, "ranking_snapshot_id": latest_snapshot_id}
    current = date_from
    while current < date_to:
        if should_cancel and should_cancel():
            break
        window_to = min(current + timedelta(days=window_days), date_to)
        remaining_matches = max(0, max_matches_per_window - totals.saved)
        if remaining_matches == 0:
            break
        completed = _completed_day(session, cursor_name, current, window_to, scope) if not dry_run else None
        if completed is not None:
            result = {
                "pages": 0,
                "checked": 0,
                "matched_top30": 0,
                "saved": 0,
                "skipped": 0,
                "errors": 0,
                "new_matches": 0,
                "updated_matches": 0,
            }
            status = "skipped_complete"
        else:
            def report_window(item: dict[str, object]) -> None:
                if not progress:
                    return
                page = int(item.get("page", 0))
                page_size = int(item.get("page_size", 0))
                page_processed = int(item.get("page_processed", 0))
                denominator = max_pages_per_window if item.get("has_next_page") else max(1, page)
                page_fraction = (page_processed / page_size) if page_size else 0.0
                window_fraction = min(0.99, max(0.0, ((max(0, page - 1) + page_fraction) / max(1, denominator))))
                elapsed = max(0.0, time.monotonic() - started)
                completed_equivalent = totals.windows + window_fraction
                average = elapsed / completed_equivalent if completed_equivalent else 0.0
                remaining = max(0.0, total_windows - completed_equivalent)
                live_totals = totals.as_dict()
                for key in ["checked", "matched_top30", "saved", "skipped", "errors", "new_matches", "updated_matches"]:
                    live_totals[key] = int(live_totals.get(key, 0)) + int(item.get(key, 0))
                progress(
                    {
                        "stage": "backfill",
                        "window_from": current.isoformat(),
                        "window_to": window_to.isoformat(),
                        "current_day": _day_key(current),
                        "current_series_id": item.get("current_series_id"),
                        "window_status": "running",
                        "page": page,
                        "pages_limit": max_pages_per_window,
                        "page_processed": page_processed,
                        "page_size": page_size,
                        "total_windows": total_windows,
                        "completed_windows": totals.windows,
                        "progress_percent": round((completed_equivalent / total_windows) * 100, 2) if total_windows else 100,
                        "eta_seconds": round(average * remaining, 1),
                        "elapsed_seconds": round(elapsed, 1),
                        "totals": live_totals,
                    }
                )

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
                progress=report_window,
                checkpoint=checkpoint,
                should_cancel=should_cancel,
            )
            if result.get("cancelled"):
                status = "cancelled"
            elif result.get("limit_reached"):
                status = "partial_limit"
            else:
                status = "complete" if int(result.get("errors", 0)) == 0 else "partial"
            if not dry_run:
                _save_day_result(session, cursor_name, current, window_to, result, status, scope)
        totals.add(current, window_to, result, status=status)
        if not dry_run:
            cursor.date_from = original_from
            cursor.date_to = date_to
            if status in {"complete", "skipped_complete"}:
                cursor.last_successful_to = window_to
            cursor.last_run_at = utcnow_naive()
            cursor.last_result_json = json.dumps(totals.as_dict(), ensure_ascii=False)
            session.flush()
            if checkpoint:
                checkpoint()
        if progress:
            completed_windows = totals.windows
            elapsed = max(0.0, time.monotonic() - started)
            average = elapsed / completed_windows if completed_windows else 0.0
            remaining = max(0, total_windows - completed_windows)
            progress(
                {
                    "stage": "backfill",
                    "window_from": current.isoformat(),
                    "window_to": window_to.isoformat(),
                    "current_day": _day_key(current),
                    "window_status": status,
                    "total_windows": total_windows,
                    "completed_windows": completed_windows,
                    "progress_percent": round((completed_windows / total_windows) * 100, 2) if total_windows else 100,
                    "eta_seconds": round(average * remaining, 1),
                    "elapsed_seconds": round(elapsed, 1),
                    "totals": totals.as_dict(),
                }
            )
        if totals.saved >= max_matches_per_window:
            break
        if status == "cancelled":
            break
        current = window_to
    output = totals.as_dict()
    output["cancelled"] = bool(should_cancel and should_cancel())
    output["complete"] = (
        not output["cancelled"]
        and len(totals.window_results) == total_windows
        and all(item["status"] in {"complete", "skipped_complete"} for item in totals.window_results)
    )
    if output["cancelled"]:
        output["stop_reason"] = "cancelled"
    elif totals.saved >= max_matches_per_window and not output["complete"]:
        output["stop_reason"] = "max_matches_reached"
    elif not output["complete"]:
        output["stop_reason"] = "partial_windows"
    else:
        output["stop_reason"] = None
    return output


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

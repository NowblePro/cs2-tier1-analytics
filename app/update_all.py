from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.grid import GridClient, refresh_live_grid_matches
from app.jobs import run_post_sync_pipeline
from app.pandascore import PandaScoreClient, ingest_past_pandascore_results, ingest_upcoming_with_histories
from app.valve_vrs import ValveVrsClient, ingest_latest_valve_ranking


def run_update_all(
    session: Session,
    *,
    valve_client: ValveVrsClient,
    pandascore_client: PandaScoreClient,
    grid_client: GridClient,
    top_limit: int = 50,
    upcoming_days: int = 14,
    results_days: int = 7,
    participant_history_days: int = 180,
    max_matches: int = 500,
    dry_run: bool = False,
    refresh_stats: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    result: dict[str, Any] = {}

    def report(phase: str, item: dict[str, Any] | None = None) -> None:
        if progress:
            progress({"phase": phase, **(item or {})})

    report("ranking", {"stage": "valve-ranking"})
    result["ranking"] = ingest_latest_valve_ranking(
        session, valve_client, limit=100, dry_run=dry_run, progress=lambda item: report("ranking", item)
    )
    if should_cancel and should_cancel():
        return {**result, "cancelled": True}

    report("upcoming", {"stage": "pandascore-upcoming"})
    result["upcoming"] = ingest_upcoming_with_histories(
        session,
        pandascore_client,
        now,
        now + timedelta(days=upcoming_days),
        max_pages=5,
        max_matches=max_matches,
        history_days=participant_history_days,
        history_max_pages=2,
        history_max_matches=min(max_matches, 100),
        top_limit=top_limit,
        dry_run=dry_run,
        progress=lambda item: report("upcoming", item),
        should_cancel=should_cancel,
    )
    if should_cancel and should_cancel():
        return {**result, "cancelled": True}

    report("results", {"stage": "pandascore-results"})
    result["results"] = ingest_past_pandascore_results(
        session,
        pandascore_client,
        now - timedelta(days=results_days),
        now,
        max_pages=10,
        max_matches=max_matches,
        top_limit=top_limit,
        dry_run=dry_run,
        progress=lambda item: report("results", item),
        should_cancel=should_cancel,
    )
    if should_cancel and should_cancel():
        return {**result, "cancelled": True}

    report("live", {"stage": "grid-live"})
    result["live"] = refresh_live_grid_matches(
        session, grid_client, limit=min(max_matches, 100), dry_run=dry_run, should_cancel=should_cancel
    )
    if should_cancel and should_cancel():
        return {**result, "cancelled": True}

    if not dry_run:
        report("post_pipeline", {"stage": "aliases"})
        result["post_pipeline"] = run_post_sync_pipeline(
            session,
            grid_client,
            stats_limit=top_limit,
            refresh_stats_enabled=refresh_stats,
            progress=lambda item: report("post_pipeline", item),
            should_cancel=should_cancel,
        )
    result["cancelled"] = bool(should_cancel and should_cancel())
    return result

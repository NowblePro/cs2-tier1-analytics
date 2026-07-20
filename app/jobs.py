from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.grid.client import GridClient
from app.grid.stats import refresh_grid_stats
from app.metrics import compute_metrics
from app.models.schema import JobRun
from app.repositories.team_aliases import merge_team_aliases
from app.validation import validate_data


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def create_job_run(session: Session, *, job_id: str, kind: str, request: dict[str, Any]) -> JobRun:
    row = JobRun(
        job_id=job_id,
        kind=kind,
        status="queued",
        request_json=json.dumps(request, ensure_ascii=False, sort_keys=True),
        created_at=utc_now(),
    )
    session.add(row)
    return row


def update_job_run(
    session: Session,
    job_id: str,
    *,
    status: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    progress: dict[str, Any] | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    row = session.scalar(select(JobRun).where(JobRun.job_id == job_id))
    if row is None:
        return
    now = utc_now()
    if status:
        row.status = status
    if started:
        row.started_at = now
    if finished:
        row.finished_at = now
        if row.started_at:
            row.duration_seconds = (row.finished_at - row.started_at).total_seconds()
    if result is not None:
        row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if error is not None:
        row.error = error
    if progress is not None:
        row.progress_json = json.dumps(progress, ensure_ascii=False, sort_keys=True)


def serialize_job_run(row: JobRun) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "kind": row.kind,
        "status": row.status,
        "request": json.loads(row.request_json) if row.request_json else None,
        "result": json.loads(row.result_json) if row.result_json else None,
        "error": row.error,
        "progress": json.loads(row.progress_json) if row.progress_json else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_seconds": row.duration_seconds,
    }


def run_post_sync_pipeline(
    session: Session,
    client: GridClient | None,
    *,
    stats_window: str = "LAST_MONTH",
    stats_limit: int = 50,
    validate_report_dir: Path = Path("data/reports"),
    refresh_stats_enabled: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["team_aliases"] = merge_team_aliases(session, dry_run=False)
    result["metrics"] = {"teams": compute_metrics(session)}
    if refresh_stats_enabled and client is not None:
        result["stats_refresh"] = refresh_grid_stats(
            session=session,
            client=client,
            entity_type="team",
            window_name=stats_window,
            limit=stats_limit,
            dry_run=False,
        )
    output = validate_report_dir / f"validation-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
    result["validation"] = validate_data(session, output)
    result["validation_report"] = str(output)
    return result

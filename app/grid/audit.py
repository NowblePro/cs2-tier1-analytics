from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.grid.client import GridClient
from app.grid.ingest import _series_involves_top_team, _series_is_cs2, latest_top_team_names
from app.models.schema import Match


def audit_grid_period(
    session: Session,
    client: GridClient,
    date_from: datetime,
    date_to: datetime,
    *,
    max_pages: int = 50,
    top_limit: int = 50,
    require_top_team: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    top_names = latest_top_team_names(session, top_limit)
    if require_top_team and not top_names:
        raise RuntimeError(f"No top-{top_limit} ranking snapshot found. Load ranking first.")

    local_matches = session.scalars(select(Match).where(Match.source_url.like("grid://series/%"))).all()
    local_by_source = {match.source_url: match for match in local_matches}
    page = checked = expected = present = 0
    after = None
    missing: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "present": 0, "missing": 0, "invalid": 0})
    cancelled = False

    while page < max_pages:
        if should_cancel and should_cancel():
            cancelled = True
            break
        summaries, page_info = client.list_series(date_from, date_to, first=50, after=after)
        page += 1
        for summary in summaries:
            checked += 1
            relevant = _series_involves_top_team(summary, top_names) if require_top_team else _series_is_cs2(summary)
            if not relevant:
                continue
            expected += 1
            source_url = f"grid://series/{summary.id}"
            day = (summary.start_time_scheduled or "unknown")[:10]
            by_day[day]["expected"] += 1
            local_match = local_by_source.get(source_url)
            if local_match is not None:
                present += 1
                by_day[day]["present"] += 1
                reasons = []
                if local_match.team1_id is None or local_match.team2_id is None:
                    reasons.append("missing_teams")
                if local_match.status == "completed" and local_match.winner_team_id is None:
                    reasons.append("completed_without_winner")
                if reasons:
                    by_day[day]["invalid"] += 1
                    invalid.append({"series_id": summary.id, "match_id": local_match.id, "reasons": reasons})
            else:
                by_day[day]["missing"] += 1
                missing.append({
                    "series_id": summary.id,
                    "scheduled_at": summary.start_time_scheduled,
                    "tournament": summary.tournament_name,
                    "teams": [(team.get("baseInfo") or {}).get("name") for team in summary.teams if (team.get("baseInfo") or {}).get("name")],
                })
        if progress:
            progress({
                "stage": "audit",
                "page": page,
                "pages_limit": max_pages,
                "checked": checked,
                "expected": expected,
                "present": present,
                "missing": len(missing),
                "invalid": len(invalid),
                "progress_percent": 100.0 if not page_info.get("hasNextPage") else round(page / max_pages * 100, 2),
            })
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")

    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "pages": page,
        "checked": checked,
        "expected": expected,
        "present": present,
        "missing_count": len(missing),
        "invalid_count": len(invalid),
        "coverage_percent": round((present / expected) * 100, 2) if expected else 100.0,
        "complete": not cancelled and not missing and not invalid,
        "cancelled": cancelled,
        "days": [{"day": day, **counts} for day, counts in sorted(by_day.items())],
        "missing_series": missing,
        "invalid_matches": invalid,
    }

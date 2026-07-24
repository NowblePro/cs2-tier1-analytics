from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.backup import create_database_backup, export_analytics_data
from app.config import get_settings
from app.metrics import compute_metrics
from app.quality import period_quality_report
from app.validation import validate_data
from app.web.database import session_factory


router = APIRouter(prefix="/api", tags=["operations"])


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


@router.post("/backup")
def backup_database():
    return create_database_backup(get_settings())


@router.post("/export")
def export_database():
    Session = session_factory()
    with Session() as session:
        return export_analytics_data(session)


@router.post("/metrics/compute")
def compute_metrics_endpoint():
    Session = session_factory()
    with Session.begin() as session:
        count = compute_metrics(session)
    return {"ok": True, "teams": count}


@router.get("/validate")
def validate_endpoint():
    Session = session_factory()
    with Session() as session:
        return validate_data(session)


@router.get("/data-quality/period")
def period_quality(
    days: int = 30,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    team_id: int | None = None,
    candidate_limit: int = 100,
):
    end = _naive_utc(date_to) if date_to else datetime.now(UTC).replace(tzinfo=None)
    start = _naive_utc(date_from) if date_from else end - timedelta(days=max(1, min(days, 3650)))
    Session = session_factory()
    with Session() as session:
        return period_quality_report(
            session,
            start,
            end,
            team_id=team_id,
            candidate_limit=max(1, min(candidate_limit, 500)),
        )

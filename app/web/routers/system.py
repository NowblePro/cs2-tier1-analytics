from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.web.database import session_factory


router = APIRouter(tags=["system"])


@router.get("/healthz")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "cs2-tier1-analytics",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/readyz")
def readiness():
    try:
        Session = session_factory()
        with Session() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unavailable"},
        )
    return {"status": "ready", "database": "available"}

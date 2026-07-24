from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.schema import Event, Match, MatchMap, Player, PlayerMapStat, RankingSnapshot, RankingSnapshotTeam, Round, Team


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def create_database_backup(settings: Settings, output_dir: Path = Path("data/backups")) -> dict[str, Any]:
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("Automatic backup currently supports SQLite only")
    source = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    if not source.exists():
        raise RuntimeError(f"Database file does not exist: {source}")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = (output_dir / f"cs2-{_stamp()}.db").resolve()
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
        source_db.backup(destination_db)
    return {"ok": True, "path": str(destination), "size_bytes": destination.stat().st_size}


def _csv_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def export_analytics_data(session: Session, output_dir: Path = Path("data/exports")) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = (output_dir / f"cs2-export-{_stamp()}.zip").resolve()
    models = [Team, Event, Match, MatchMap, Round, Player, PlayerMapStat, RankingSnapshot, RankingSnapshotTeam]
    counts: dict[str, int] = {}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for model in models:
            columns = [column.key for column in inspect(model).columns]
            rows = session.scalars(select(model)).all()
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _csv_value(getattr(row, column)) for column in columns})
            archive.writestr(f"{model.__tablename__}.csv", buffer.getvalue().encode("utf-8-sig"))
            counts[model.__tablename__] = len(rows)
        archive.writestr(
            "manifest.json",
            json.dumps({"created_at": datetime.now(UTC).isoformat(), "tables": counts}, ensure_ascii=False, indent=2),
        )
    return {"ok": True, "path": str(destination), "size_bytes": destination.stat().st_size, "tables": counts}

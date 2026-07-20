from datetime import datetime

from sqlalchemy import select

from app.grid.backfill import run_grid_backfill, run_grid_update_since_cursor
from app.models.schema import GridSyncCursor, Match
from tests.test_grid_ingest import FakeGridClient, add_ranking, session_factory


def test_grid_backfill_updates_cursor_and_saves_match():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        result = run_grid_backfill(
            session,
            FakeGridClient(),
            datetime(2026, 7, 17),
            datetime(2026, 7, 18),
            max_pages_per_window=1,
            max_matches_per_window=5,
            top_limit=50,
        )
        cursor = session.scalar(select(GridSyncCursor).where(GridSyncCursor.name == "grid-main"))
        assert result["windows"] == 1
        assert cursor.last_successful_to == datetime(2026, 7, 18)
        assert len(session.scalars(select(Match)).all()) == 1


def test_grid_update_uses_cursor():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        cursor = GridSyncCursor(name="grid-main", last_successful_to=datetime(2026, 7, 17))
        session.add(cursor)
        result = run_grid_update_since_cursor(
            session,
            FakeGridClient(),
            max_pages=1,
            max_matches=5,
            top_limit=50,
        )
        assert result["from"] == "2026-07-17T00:00:00"
        assert cursor.last_successful_to is not None

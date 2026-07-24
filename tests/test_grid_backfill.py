from datetime import datetime

from sqlalchemy import select

from app.grid.backfill import reset_backfill_days, run_grid_backfill, run_grid_update_since_cursor
from app.models.schema import GridBackfillDay, GridSyncCursor, Match
from tests.test_grid_ingest import FakeGridClient, add_ranking, session_factory


class CountingGridClient(FakeGridClient):
    def __init__(self):
        self.calls = 0

    def list_series(self, *args, **kwargs):
        self.calls += 1
        return super().list_series(*args, **kwargs)


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
        day = session.scalar(select(GridBackfillDay).where(GridBackfillDay.day == "2026-07-17"))
        assert day is not None
        assert day.status == "complete"


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


def test_grid_backfill_skips_completed_day():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        run_grid_backfill(
            session,
            CountingGridClient(),
            datetime(2026, 7, 17),
            datetime(2026, 7, 18),
            max_pages_per_window=1,
            max_matches_per_window=5,
            top_limit=50,
        )

    client = CountingGridClient()
    with Session.begin() as session:
        result = run_grid_backfill(
            session,
            client,
            datetime(2026, 7, 17),
            datetime(2026, 7, 18),
            max_pages_per_window=1,
            max_matches_per_window=5,
            top_limit=50,
        )
        assert client.calls == 0
        assert result["window_results"][0]["status"] == "skipped_complete"


def test_grid_backfill_reports_progress_before_window_finishes():
    Session = session_factory()
    updates = []
    with Session.begin() as session:
        add_ranking(session)
        run_grid_backfill(
            session,
            FakeGridClient(),
            datetime(2026, 7, 17),
            datetime(2026, 7, 18),
            max_pages_per_window=1,
            max_matches_per_window=5,
            top_limit=50,
            progress=updates.append,
        )
    running = [item for item in updates if item["window_status"] == "running"]
    assert running
    assert running[-1]["totals"]["checked"] == 1
    assert updates[-1]["window_status"] == "complete"


def test_grid_backfill_can_checkpoint_during_long_window():
    Session = session_factory()
    checkpoints = []
    with Session() as session:
        add_ranking(session)
        session.commit()
        run_grid_backfill(
            session,
            FakeGridClient(),
            datetime(2026, 7, 17),
            datetime(2026, 7, 18),
            max_pages_per_window=1,
            max_matches_per_window=5,
            top_limit=50,
            checkpoint=lambda: (session.commit(), checkpoints.append(True)),
        )
    assert len(checkpoints) >= 2
    with Session() as session:
        assert len(session.scalars(select(Match)).all()) == 1


def test_reset_backfill_days_marks_selected_period_pending():
    Session = session_factory()
    with Session.begin() as session:
        session.add(GridBackfillDay(cursor_name="grid-main", day="2026-07-17", date_from=datetime(2026, 7, 17), date_to=datetime(2026, 7, 18), status="complete", completed_at=datetime(2026, 7, 18)))
    with Session.begin() as session:
        assert reset_backfill_days(session, datetime(2026, 7, 17), datetime(2026, 7, 17, 23, 59)) == 1
    with Session() as session:
        day = session.scalar(select(GridBackfillDay))
        assert day.status == "pending"
        assert day.completed_at is None

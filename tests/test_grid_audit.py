from datetime import datetime

from app.grid.audit import audit_grid_period
from app.models.schema import Match, Team
from tests.test_grid_ingest import FakeGridClient, add_ranking, session_factory


def test_grid_period_audit_reports_missing_and_coverage():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        result = audit_grid_period(session, FakeGridClient(), datetime(2026, 7, 17), datetime(2026, 7, 18), max_pages=1)
    assert result["expected"] == 1
    assert result["missing_count"] == 1
    assert result["coverage_percent"] == 0


def test_grid_period_audit_recognizes_saved_series():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        other = Team(hltv_team_id=-2, name="Other Team")
        session.add(other)
        session.flush()
        session.add(Match(hltv_match_id=-1, source_url="grid://series/series-1", status="scheduled", team1_id=1, team2_id=other.id))
        result = audit_grid_period(session, FakeGridClient(), datetime(2026, 7, 17), datetime(2026, 7, 18), max_pages=1)
    assert result["complete"] is True
    assert result["coverage_percent"] == 100


def test_grid_period_audit_flags_invalid_local_match():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        session.add(Match(hltv_match_id=-1, source_url="grid://series/series-1", status="completed"))
        result = audit_grid_period(session, FakeGridClient(), datetime(2026, 7, 17), datetime(2026, 7, 18), max_pages=1)
    assert result["present"] == 1
    assert result["invalid_count"] == 1
    assert result["complete"] is False

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.grid.stats import refresh_grid_stats
from app.models.schema import Base, GridEntityMap, GridStatsSnapshot


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


class FakeStatsClient:
    def __init__(self):
        self.team_calls = []
        self.player_calls = []

    def team_statistics(self, team_id, window_name):
        self.team_calls.append((team_id, window_name))
        return {
            "id": team_id,
            "series": {
                "count": 3,
                "kills": {"sum": 100, "avg": 33.3},
                "deaths": {"sum": 80, "avg": 26.6},
                "won": [{"value": True, "count": 2, "percentage": 66.67}],
            },
            "game": {"count": 7},
            "segment": [],
        }

    def player_statistics(self, player_id, window_name):
        self.player_calls.append((player_id, window_name))
        return {"id": player_id, "series": {"count": 2}, "game": {"count": 4}}


def test_refresh_grid_stats_saves_numeric_grid_team_ids_only():
    Session = session_factory()
    client = FakeStatsClient()
    with Session.begin() as session:
        session.add(GridEntityMap(entity_type="team", grid_id="local-1", local_table="teams", local_id=1, name="Local"))
        session.add(GridEntityMap(entity_type="team", grid_id="123", local_table="teams", local_id=1, name="Team A"))
        result = refresh_grid_stats(session, client, entity_type="team", window_name="LAST_MONTH", limit=10)

    assert result == {"checked": 1, "saved": 1, "skipped": 0, "errors": 0}
    assert client.team_calls == [("123", "LAST_MONTH")]
    with Session() as session:
        row = session.scalar(select(GridStatsSnapshot).where(GridStatsSnapshot.grid_id == "123"))
        assert row is not None
        assert row.name == "Team A"
        assert '"count":3' in row.payload_json


def test_refresh_grid_stats_is_idempotent_per_entity_window():
    Session = session_factory()
    client = FakeStatsClient()
    with Session.begin() as session:
        session.add(GridEntityMap(entity_type="team", grid_id="123", name="Team A"))
        refresh_grid_stats(session, client, entity_type="team", window_name="LAST_MONTH", limit=10)
        refresh_grid_stats(session, client, entity_type="team", window_name="LAST_MONTH", limit=10)

    with Session() as session:
        rows = session.scalars(select(GridStatsSnapshot)).all()
        assert len(rows) == 1
        assert rows[0].grid_id == "123"


def test_refresh_grid_stats_dry_run_does_not_call_api_or_write():
    Session = session_factory()
    client = FakeStatsClient()
    with Session.begin() as session:
        session.add(GridEntityMap(entity_type="player", grid_id="999", name="Player A"))
        result = refresh_grid_stats(session, client, entity_type="player", dry_run=True)

    assert result == {"checked": 1, "saved": 0, "skipped": 1, "errors": 0}
    assert client.player_calls == []
    with Session() as session:
        assert session.scalars(select(GridStatsSnapshot)).all() == []


def test_refresh_grid_stats_skips_steam64_player_ids():
    Session = session_factory()
    client = FakeStatsClient()
    with Session.begin() as session:
        session.add(GridEntityMap(entity_type="player", grid_id="76561199123003921", name="Steam Player"))
        result = refresh_grid_stats(session, client, entity_type="player", window_name="LAST_MONTH", limit=10)

    assert result == {"checked": 1, "saved": 0, "skipped": 1, "errors": 0}
    assert client.player_calls == []
    with Session() as session:
        assert session.scalars(select(GridStatsSnapshot)).all() == []

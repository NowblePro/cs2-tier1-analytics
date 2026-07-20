from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.grid.client import GridSeriesSummary
from app.grid.ingest import _series_is_cs2, grid_state_to_match, ingest_recent_grid_series, save_grid_identity_maps, save_raw_grid_state
from app.models.schema import Base, GridEntityMap, GridRawSeriesState, Match, RankingSnapshot, RankingSnapshotTeam, Team


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


class FakeGridClient:
    def list_series(self, date_from, date_to, first=50, after=None):
        return (
            [
                GridSeriesSummary(
                    id="series-1",
                    start_time_scheduled="2026-07-17T12:00:00Z",
                    tournament_name="GRID Test",
                    title_name="Counter-Strike 2",
                    teams=[
                        {"baseInfo": {"id": "grid-team-falcons", "name": "Falcons"}},
                        {"baseInfo": {"id": "grid-team-other", "name": "Other Team"}},
                    ],
                )
            ],
            {"hasNextPage": False, "endCursor": None},
        )

    def series_state(self, series_id):
        return {
            "id": series_id,
            "finished": True,
            "teams": [
                {"team": {"id": "grid-team-falcons", "name": "Falcons"}, "score": 1, "won": True},
                {"team": {"id": "grid-team-other", "name": "Other Team"}, "score": 0, "won": False},
            ],
            "games": [
                {
                    "id": "game-1",
                    "map": {"id": "de_mirage", "name": "de_mirage"},
                    "teams": [
                        {
                            "id": "grid-team-falcons",
                            "name": "Falcons",
                            "score": 13,
                            "won": True,
                            "players": [
                                {
                                    "id": "p1",
                                    "name": "player1",
                                    "kills": 20,
                                    "deaths": 10,
                                    "killAssistsGiven": 4,
                                    "damageDealt": 1800,
                                    "headshots": 10,
                                }
                            ],
                        },
                        {
                            "id": "grid-team-other",
                            "name": "Other Team",
                            "score": 8,
                            "won": False,
                            "players": [{"id": "p2", "name": "player2", "kills": 10, "deaths": 20}],
                        },
                    ],
                }
            ],
        }


def add_ranking(session):
    falcons = Team(hltv_team_id=11283, name="Falcons")
    session.add(falcons)
    session.flush()
    snapshot = RankingSnapshot(ranking_date=datetime(2026, 7, 17), source_url="fixture")
    session.add(snapshot)
    session.flush()
    session.add(RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=falcons.id, rank=1, points=900))


def test_grid_state_maps_to_match_dto():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        summary, _ = FakeGridClient().list_series(datetime(2026, 7, 17), datetime(2026, 7, 18))
        dto = grid_state_to_match(summary[0], FakeGridClient().series_state("series-1"), session)
    assert dto.status == "completed"
    assert dto.team1.name == "Falcons"
    assert dto.maps[0].name == "Mirage"
    assert dto.maps[0].score_team1 == 13
    assert dto.player_stats[0].assists == 4
    assert dto.player_stats[0].adr == 85.71
    assert dto.player_stats[0].headshot_percentage == 50.0
    assert dto.player_stats[0].kd_ratio == 2.0


def test_grid_cs2_title_detection_accepts_grid_open_access_name():
    summary = GridSeriesSummary(
        id="series-cs2",
        start_time_scheduled="2026-07-17T12:00:00Z",
        tournament_name="GRID Test",
        title_name="Counter Strike 2",
        teams=[],
    )
    assert _series_is_cs2(summary) is True


def test_grid_empty_finished_state_is_not_completed_without_winner():
    Session = session_factory()
    summary = GridSeriesSummary(
        id="series-empty",
        start_time_scheduled="2026-07-17T12:00:00Z",
        tournament_name="GRID Test",
        title_name="Counter Strike 2",
        teams=[
            {"baseInfo": {"id": "grid-team-a", "name": "Team A"}},
            {"baseInfo": {"id": "grid-team-b", "name": "Team B"}},
        ],
    )
    state = {
        "id": "series-empty",
        "started": False,
        "finished": True,
        "teams": [{"score": 0, "won": False}, {"score": 0, "won": False}],
        "games": [],
    }
    with Session() as session:
        dto = grid_state_to_match(summary, state, session)
    assert dto.status == "scheduled"
    assert dto.winner_hltv_team_id is None


def test_ingest_recent_grid_series_filters_top30_and_saves():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        result = ingest_recent_grid_series(session, FakeGridClient(), datetime(2026, 7, 17), datetime(2026, 7, 18), max_pages=1, max_matches=5)
    with Session() as session:
        assert result["saved"] == 1
        assert len(session.scalars(select(Match)).all()) == 1
        assert session.scalar(select(GridEntityMap).where(GridEntityMap.entity_type == "series", GridEntityMap.grid_id == "series-1")) is not None
        assert session.scalar(select(GridEntityMap).where(GridEntityMap.entity_type == "player", GridEntityMap.grid_id == "p1")) is not None


def test_save_raw_grid_state_deduplicates_by_payload_hash():
    Session = session_factory()
    state = FakeGridClient().series_state("series-1")
    with Session.begin() as session:
        first = save_raw_grid_state(session, "series-1", state)
        session.flush()
        second = save_raw_grid_state(session, "series-1", state)
        session.flush()
        assert first.id == second.id
        assert len(session.scalars(select(GridRawSeriesState)).all()) == 1
        assert first.has_games is True
        assert first.has_maps is True
        assert first.has_players is True


def test_save_grid_identity_maps_deduplicates_pending_entities():
    Session = session_factory()
    client = FakeGridClient()
    summary, _ = client.list_series(datetime(2026, 7, 17), datetime(2026, 7, 18))
    state = client.series_state("series-1")
    with Session.begin() as session:
        add_ranking(session)
        dto = grid_state_to_match(summary[0], state, session)
        save_grid_identity_maps(session, summary[0], state, dto)
        save_grid_identity_maps(session, summary[0], state, dto)
    with Session() as session:
        rows = session.scalars(select(GridEntityMap).where(GridEntityMap.entity_type == "team", GridEntityMap.grid_id == "grid-team-falcons")).all()
        assert len(rows) == 1

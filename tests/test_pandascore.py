from datetime import datetime

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.schema import Base, ExternalEntityMap, Match, RankingSnapshot, RankingSnapshotTeam, Team
from app.pandascore.client import PandaScoreClient
from app.pandascore.ingest import ingest_past_pandascore_results, ingest_team_pandascore_history, ingest_upcoming_pandascore_matches, ingest_upcoming_with_histories


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def upcoming_item(match_id: int = 1596632):
    return {
        "id": match_id,
        "name": "Falcons vs Vitality",
        "status": "not_started",
        "scheduled_at": "2026-07-25T18:00:00Z",
        "number_of_games": 3,
        "opponents": [
            {"opponent": {"id": 1, "name": "Team Falcons", "image_url": "https://example/falcons.png"}},
            {"opponent": {"id": 2, "name": "Vitality", "image_url": None}},
        ],
        "league": {"name": "BLAST Premier"},
        "serie": {"full_name": "Bounty Season 2 2026"},
        "tournament": {"name": "Round of 16"},
    }


class FakePandaScoreClient:
    def __init__(self, items):
        self.items = items

    def list_upcoming(self, date_from, date_to, *, page=1, per_page=100):
        return (self.items if page == 1 else []), len(self.items)

    def list_past(self, date_from, date_to, *, page=1, per_page=100, team_id=None):
        return (self.items if page == 1 else []), len(self.items)

    def search_teams(self, name):
        return [{"id": 1, "name": "Team Falcons"}]


def add_ranking(session):
    falcons = Team(hltv_team_id=11283, name="Falcons")
    session.add(falcons)
    session.flush()
    snapshot = RankingSnapshot(ranking_date=datetime(2026, 7, 20), source_url="fixture")
    session.add(snapshot)
    session.flush()
    session.add(RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=falcons.id, rank=1, points=1000))
    session.flush()
    return falcons


def test_client_sends_token_and_date_range():
    captured = {}

    def handler(request: httpx.Request):
        captured["authorization"] = request.headers.get("Authorization")
        captured["query"] = str(request.url.query)
        return httpx.Response(200, json=[], headers={"X-Total": "0"})

    settings = Settings(pandascore_api_key="test-token", pandascore_base_url="https://example.test", max_retries=0)
    with PandaScoreClient(settings, transport=httpx.MockTransport(handler)) as client:
        items, total = client.list_upcoming(datetime(2026, 7, 20), datetime(2026, 8, 3))
    assert items == []
    assert total == 0
    assert captured["authorization"] == "Bearer test-token"
    assert "range%5Bscheduled_at%5D" in captured["query"]


def test_ingest_filters_by_ranking_and_is_idempotent():
    Session = session_factory()
    client = FakePandaScoreClient([upcoming_item(), {**upcoming_item(2), "opponents": [{"opponent": {"id": 3, "name": "Other A"}}, {"opponent": {"id": 4, "name": "Other B"}}]}])
    with Session.begin() as session:
        falcons = add_ranking(session)
        first = ingest_upcoming_pandascore_matches(session, client, datetime(2026, 7, 20), datetime(2026, 8, 3), top_limit=50)
        second = ingest_upcoming_pandascore_matches(session, client, datetime(2026, 7, 20), datetime(2026, 8, 3), top_limit=50)
        session.flush()
        matches = session.scalars(select(Match)).all()
        mappings = session.scalars(select(ExternalEntityMap).where(ExternalEntityMap.provider == "pandascore")).all()
    assert first["new_matches"] == 1
    assert second["updated_matches"] == 1
    assert len(matches) == 1
    assert matches[0].team1_id == falcons.id
    assert matches[0].best_of == 3
    assert matches[0].event_id is not None
    assert len(mappings) == 3


def test_ingest_attaches_to_existing_grid_schedule_without_overwriting_source():
    Session = session_factory()
    with Session.begin() as session:
        falcons = add_ranking(session)
        vitality = Team(hltv_team_id=9565, name="Vitality")
        session.add(vitality)
        session.flush()
        existing = Match(
            hltv_match_id=-10,
            source_url="grid://series/abc",
            match_time=datetime(2026, 7, 25, 18),
            status="scheduled",
            team1_id=falcons.id,
            team2_id=vitality.id,
        )
        session.add(existing)
        session.flush()
        result = ingest_upcoming_pandascore_matches(
            session,
            FakePandaScoreClient([upcoming_item()]),
            datetime(2026, 7, 20),
            datetime(2026, 8, 3),
            top_limit=50,
        )
        matches = session.scalars(select(Match)).all()
    assert result["new_matches"] == 0
    assert len(matches) == 1
    assert matches[0].source_url == "grid://series/abc"


def test_past_results_update_existing_match_without_duplicate():
    Session = session_factory()
    finished = {
        **upcoming_item(),
        "status": "finished",
        "winner_id": 1,
        "results": [{"team_id": 1, "score": 2}, {"team_id": 2, "score": 1}],
    }
    with Session.begin() as session:
        add_ranking(session)
        ingest_upcoming_pandascore_matches(
            session, FakePandaScoreClient([upcoming_item()]), datetime(2026, 7, 20), datetime(2026, 8, 3)
        )
        result = ingest_past_pandascore_results(
            session, FakePandaScoreClient([finished]), datetime(2026, 7, 20), datetime(2026, 8, 3)
        )
        matches = session.scalars(select(Match)).all()
    assert result["new_matches"] == 0
    assert result["updated_matches"] == 1
    assert len(matches) == 1
    assert matches[0].status == "completed"
    assert (matches[0].score_team1, matches[0].score_team2) == (2, 1)
    assert matches[0].winner_team_id == matches[0].team1_id


def test_team_history_uses_provider_team_filter_and_saves_results():
    Session = session_factory()
    finished = {**upcoming_item(), "status": "finished", "winner_id": 1, "results": [{"team_id": 1, "score": 2}, {"team_id": 2, "score": 0}]}
    with Session.begin() as session:
        falcons = add_ranking(session)
        result = ingest_team_pandascore_history(
            session, FakePandaScoreClient([finished]), falcons, datetime(2026, 7, 1), datetime(2026, 8, 1)
        )
        matches = session.scalars(select(Match)).all()
    assert result["saved"] == 1
    assert result["team"] == "Falcons"
    assert len(matches) == 1


def test_upcoming_pipeline_loads_history_for_both_participants():
    Session = session_factory()
    with Session.begin() as session:
        add_ranking(session)
        result = ingest_upcoming_with_histories(
            session,
            FakePandaScoreClient([upcoming_item()]),
            datetime(2026, 7, 20),
            datetime(2026, 8, 3),
            history_days=90,
            history_max_pages=1,
        )
    assert result["upcoming"]["saved"] == 1
    assert result["teams"] == 2
    assert len(result["histories"]) == 2
    assert result["history_errors"] == 0

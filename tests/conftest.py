from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="cs2-analytics-tests-"))
_TEST_DATABASE = _TEST_DATA_DIR / "web.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DATABASE.as_posix()}"

from app.models.schema import (  # noqa: E402
    Base,
    Event,
    Match,
    MatchMap,
    Player,
    PlayerMapStat,
    RankingSnapshot,
    RankingSnapshotTeam,
    Round,
    Team,
)


def _seed_web_database() -> None:
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC).replace(tzinfo=None)

    with Session.begin() as session:
        team1 = Team(hltv_team_id=1001, name="Falcons", country="International")
        team2 = Team(hltv_team_id=1002, name="Spirit", country="Russia")
        session.add_all([team1, team2])
        session.flush()

        snapshot = RankingSnapshot(ranking_date=now, source_url="test://ranking")
        session.add(snapshot)
        session.flush()
        session.add_all(
            [
                RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=team1.id, rank=1, points=1000),
                RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=team2.id, rank=2, points=900),
            ]
        )

        event = Event(hltv_event_id=2001, name="Test Championship")
        session.add(event)
        session.flush()

        completed = Match(
            hltv_match_id=3001,
            match_time=now - timedelta(days=1),
            status="completed",
            team1_id=team1.id,
            team2_id=team2.id,
            winner_team_id=team1.id,
            event_id=event.id,
            best_of=1,
            score_team1=1,
            score_team2=0,
            source_url="test://match/3001",
        )
        upcoming = Match(
            hltv_match_id=3002,
            match_time=now + timedelta(days=1),
            status="scheduled",
            team1_id=team1.id,
            team2_id=team2.id,
            event_id=event.id,
            best_of=3,
            source_url="test://match/3002",
        )
        session.add_all([completed, upcoming])
        session.flush()

        match_map = MatchMap(
            match_id=completed.id,
            hltv_mapstats_id=4001,
            map_number=1,
            name="Mirage",
            picked_by_team_id=team1.id,
            winner_team_id=team1.id,
            score_team1=13,
            score_team2=9,
            first_half_team1=7,
            first_half_team2=5,
            second_half_team1=6,
            second_half_team2=4,
        )
        session.add(match_map)
        session.flush()

        player1 = Player(hltv_player_id=5001, nickname="alpha", current_team_id=team1.id)
        player2 = Player(hltv_player_id=5002, nickname="bravo", current_team_id=team2.id)
        session.add_all([player1, player2])
        session.flush()
        session.add_all(
            [
                PlayerMapStat(
                    match_map_id=match_map.id,
                    player_id=player1.id,
                    team_id=team1.id,
                    kills=20,
                    deaths=12,
                    assists=5,
                    kd_diff=8,
                    kd_ratio=1.67,
                    adr=91.5,
                    kast=78.0,
                    rating=1.35,
                ),
                PlayerMapStat(
                    match_map_id=match_map.id,
                    player_id=player2.id,
                    team_id=team2.id,
                    kills=12,
                    deaths=20,
                    assists=3,
                    kd_diff=-8,
                    kd_ratio=0.6,
                    adr=65.0,
                    kast=62.0,
                    rating=0.75,
                ),
            ]
        )
        session.add_all(
            [
                Round(
                    match_map_id=match_map.id,
                    round_number=1,
                    half_number=1,
                    winner_team_id=team1.id,
                    winner_side="CT",
                    score_team1_after=1,
                    score_team2_after=0,
                    is_pistol=True,
                ),
                Round(
                    match_map_id=match_map.id,
                    round_number=13,
                    half_number=2,
                    winner_team_id=team2.id,
                    winner_side="CT",
                    score_team1_after=7,
                    score_team2_after=6,
                    is_pistol=True,
                ),
            ]
        )

    engine.dispose()


_seed_web_database()


def pytest_sessionfinish() -> None:
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)

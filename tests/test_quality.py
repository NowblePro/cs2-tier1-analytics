from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.grid.ingest import normalize_map_name, refresh_saved_grid_matches
from app.models.schema import Base, Match, MatchMap, Player, PlayerMapStat, Round, Team
from app.quality import normalize_saved_map_names, period_quality_report


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_period_quality_report_classifies_detail_levels():
    Session = _session_factory()
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session.begin() as session:
        team1 = Team(hltv_team_id=1, name="Falcons")
        team2 = Team(hltv_team_id=2, name="Spirit")
        session.add_all([team1, team2])
        session.flush()

        matches = []
        for index in range(5):
            match = Match(
                hltv_match_id=100 + index,
                match_time=now - timedelta(hours=index),
                status="completed",
                team1_id=team1.id,
                team2_id=team2.id,
                winner_team_id=team1.id if index != 4 else None,
                score_team1=1,
                score_team2=0,
                source_url=f"grid://series/{100 + index}",
            )
            session.add(match)
            matches.append(match)
        session.flush()

        map_only = MatchMap(
            match_id=matches[1].id,
            map_number=1,
            name="Mirage",
            score_team1=13,
            score_team2=9,
        )
        player_map = MatchMap(
            match_id=matches[2].id,
            map_number=1,
            name="Nuke",
            score_team1=13,
            score_team2=10,
        )
        round_map = MatchMap(
            match_id=matches[3].id,
            map_number=1,
            name="Ancient",
            score_team1=13,
            score_team2=8,
        )
        session.add_all([map_only, player_map, round_map])
        session.flush()

        player = Player(hltv_player_id=10, nickname="alpha", current_team_id=team1.id)
        session.add(player)
        session.flush()
        session.add_all(
            [
                PlayerMapStat(
                    match_map_id=player_map.id,
                    player_id=player.id,
                    team_id=team1.id,
                    kills=20,
                    deaths=10,
                ),
                PlayerMapStat(
                    match_map_id=round_map.id,
                    player_id=player.id,
                    team_id=team1.id,
                    kills=18,
                    deaths=12,
                ),
                Round(
                    match_map_id=round_map.id,
                    round_number=1,
                    winner_team_id=team1.id,
                    score_team1_after=1,
                    score_team2_after=0,
                ),
            ]
        )

    with Session() as session:
        report = period_quality_report(session, now - timedelta(days=1), now + timedelta(days=1))
    assert report["matches"] == 5
    assert report["levels"] == {
        "invalid": 1,
        "result": 1,
        "maps": 1,
        "players": 1,
        "rounds": 1,
    }
    assert report["repairable_count"] == 3


def test_map_aliases_and_saved_normalization():
    assert normalize_map_name("mrg") == "Mirage"
    assert normalize_map_name("default-dust2") == "Dust2"
    assert normalize_map_name("de_cbble") == "Cobblestone"

    Session = _session_factory()
    with Session.begin() as session:
        team1 = Team(hltv_team_id=1, name="A")
        team2 = Team(hltv_team_id=2, name="B")
        session.add_all([team1, team2])
        session.flush()
        match = Match(
            hltv_match_id=1,
            status="completed",
            team1_id=team1.id,
            team2_id=team2.id,
            winner_team_id=team1.id,
            source_url="grid://series/1",
        )
        session.add(match)
        session.flush()
        match_map = MatchMap(match_id=match.id, map_number=1, name="mrg")
        session.add(match_map)
        session.flush()
        map_id = match_map.id
    with Session.begin() as session:
        result = normalize_saved_map_names(session)
        assert result["changed"] == 1
    with Session() as session:
        assert session.get(MatchMap, map_id).name == "Mirage"


def test_saved_grid_repair_dry_run_does_not_contact_api():
    Session = _session_factory()
    with Session.begin() as session:
        team1 = Team(hltv_team_id=1, name="A")
        team2 = Team(hltv_team_id=2, name="B")
        session.add_all([team1, team2])
        session.flush()
        match = Match(
            hltv_match_id=1,
            match_time=datetime.now(UTC).replace(tzinfo=None),
            status="completed",
            team1_id=team1.id,
            team2_id=team2.id,
            winner_team_id=team1.id,
            source_url="grid://series/1",
        )
        session.add(match)
        session.flush()
        match_id = match.id
    with Session.begin() as session:
        result = refresh_saved_grid_matches(session, None, [match_id], dry_run=True)  # type: ignore[arg-type]
    assert result["checked"] == 1
    assert result["refreshed"] == 1

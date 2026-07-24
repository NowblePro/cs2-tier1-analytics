from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.demos import awpy_adapter
from app.demos.awpy_adapter import ParsedDemo, import_demo_to_match_map
from app.models.schema import Base, Match, MatchMap, PlayerMapStat, Round, Team


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True), engine


def _parsed_demo() -> ParsedDemo:
    return ParsedDemo(
        path="test.dem",
        header={"map_name": "de_dust2", "server_name": "Demo Server"},
        rounds=[
            {"round_num": 1, "winner": "CT", "reason": "t_killed"},
            {"round_num": 2, "winner": "T", "reason": "bomb_exploded"},
            {"round_num": 13, "winner": "CT", "reason": "ct_killed"},
        ],
        kills=[
            {"attacker_steamid": "7651", "attacker_name": "alpha", "victim_steamid": "7652", "victim_name": "bravo"},
            {"attacker_steamid": "7651", "attacker_name": "alpha", "victim_steamid": "7652", "victim_name": "bravo"},
            {"attacker_steamid": "7652", "attacker_name": "bravo", "victim_steamid": "7651", "victim_name": "alpha"},
        ],
        damages=[
            {"attacker_steamid": "7651", "attacker_name": "alpha", "dmg_health_real": 180},
            {"attacker_steamid": "7652", "attacker_name": "bravo", "dmg_health_real": 90},
        ],
    )


def test_parsed_demo_inspection_reports_available_round_data():
    report = _parsed_demo().inspect()

    assert report["map_name"] == "Dust2"
    assert report["counts"] == {"rounds": 3, "kills": 3, "damages": 2}
    assert report["round_summary"]["t_wins"] == 1
    assert report["round_summary"]["ct_wins"] == 2
    assert report["player_totals"][0]["name"] == "alpha"
    assert report["player_totals"][0]["kills"] == 2


def test_import_demo_to_existing_match_map_replaces_rounds(monkeypatch):
    Session, engine = _session()
    monkeypatch.setattr(awpy_adapter, "parse_demo_file", lambda _: _parsed_demo())

    with Session.begin() as session:
        team1 = Team(hltv_team_id=1, name="Alpha", country=None)
        team2 = Team(hltv_team_id=2, name="Bravo", country=None)
        session.add_all([team1, team2])
        session.flush()
        match = Match(
            hltv_match_id=100,
            match_time=datetime.now(UTC).replace(tzinfo=None),
            status="completed",
            team1_id=team1.id,
            team2_id=team2.id,
            source_url="test://match",
        )
        session.add(match)
        session.flush()
        session.add(MatchMap(match_id=match.id, map_number=1, name="Mirage"))
        session.flush()
        match_id = match.id

    with Session.begin() as session:
        result = import_demo_to_match_map(
            session,
            "test.dem",
            match_id=match_id,
            map_number=1,
            import_player_stats=True,
        )

    with Session() as session:
        rounds = session.scalars(select(Round).order_by(Round.round_number)).all()
        stats = session.scalars(select(PlayerMapStat)).all()
        match_map = session.scalar(select(MatchMap).where(MatchMap.match_id == match_id))

    assert result.rounds_imported == 3
    assert result.player_stats_imported == 2
    assert result.players_unmapped_to_team == 2
    assert match_map.name == "Dust2"
    assert [row.round_number for row in rounds] == [1, 2, 13]
    assert [row.is_pistol for row in rounds] == [True, False, True]
    assert {row.kills for row in stats} == {1, 2}

    engine.dispose()

from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.schema import Base, ExternalEntityMap, GridEntityMap, GridStatsSnapshot, Match, MatchMap, Team
from app.repositories.team_aliases import canonical_team_key, find_team_by_alias, merge_team_aliases


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_canonical_team_key_handles_provider_brand_variants():
    assert canonical_team_key("Aurora Gaming") == canonical_team_key("Aurora")
    assert canonical_team_key("FUT Esports") == canonical_team_key("FUT")
    assert canonical_team_key("The MongolZ") == canonical_team_key("MongolZ")
    assert canonical_team_key("BetBoom Team") == canonical_team_key("BetBoom")
    assert canonical_team_key("Pain Gaming") == canonical_team_key("paiN")


def test_find_team_by_alias_prefers_hltv_canonical_team():
    Session = session_factory()
    with Session.begin() as session:
        canonical = Team(hltv_team_id=11283, name="Falcons")
        duplicate = Team(hltv_team_id=-1, name="Team Falcons")
        session.add_all([duplicate, canonical])
        session.flush()
        found = find_team_by_alias(session, "Team Falcons")
        assert found.id == canonical.id


def test_merge_team_aliases_moves_references_and_deletes_duplicate():
    Session = session_factory()
    with Session.begin() as session:
        canonical = Team(hltv_team_id=11283, name="Falcons")
        duplicate = Team(hltv_team_id=-1, name="Team Falcons")
        session.add_all([canonical, duplicate])
        session.flush()
        session.add(Match(hltv_match_id=1, source_url="grid://series/1", team1_id=duplicate.id, team2_id=canonical.id, status="completed"))
        session.add(GridEntityMap(entity_type="team", grid_id="51967", local_table="teams", local_id=duplicate.id, name="Team Falcons"))
        session.add(GridStatsSnapshot(entity_type="team", grid_id="51967", local_table="teams", local_id=duplicate.id, name="Team Falcons", window_name="LAST_MONTH", payload_json="{}"))
        result = merge_team_aliases(session)

    assert result["deleted_teams"] == 1
    with Session() as session:
        teams = session.scalars(select(Team)).all()
        assert [(team.id, team.name) for team in teams] == [(1, "Falcons")]
        match = session.scalar(select(Match))
        assert match.team1_id == 1
        entity = session.scalar(select(GridEntityMap))
        assert entity.local_id == 1
        stats = session.scalar(select(GridStatsSnapshot))
        assert stats.local_id == 1


def test_merge_aliases_removes_pandascore_duplicate_of_detailed_grid_match():
    Session = session_factory()
    with Session.begin() as session:
        falcons = Team(hltv_team_id=1, name="Falcons")
        betboom = Team(hltv_team_id=2, name="BetBoom")
        betboom_duplicate = Team(hltv_team_id=-2, name="BetBoom Team")
        session.add_all([falcons, betboom, betboom_duplicate])
        session.flush()
        grid_match = Match(hltv_match_id=10, source_url="grid://series/10", match_time=datetime(2026, 6, 12, 15), status="completed", team1_id=falcons.id, team2_id=betboom.id, score_team1=0, score_team2=2, winner_team_id=betboom.id)
        panda_match = Match(hltv_match_id=-10, source_url="pandascore://match/10", match_time=datetime(2026, 6, 12, 15) + timedelta(minutes=10), status="completed", team1_id=falcons.id, team2_id=betboom_duplicate.id, score_team1=0, score_team2=2, winner_team_id=betboom_duplicate.id)
        session.add_all([grid_match, panda_match])
        session.flush()
        session.add(MatchMap(match_id=grid_match.id, map_number=1, name="Dust2", score_team1=10, score_team2=13, winner_team_id=betboom.id))
        session.add(ExternalEntityMap(provider="pandascore", entity_type="match", external_id="10", local_table="matches", local_id=panda_match.id))
        result = merge_team_aliases(session)
    assert result["deleted_teams"] == 1
    assert result["merged_matches"] == 1
    with Session() as session:
        matches = session.scalars(select(Match)).all()
        assert len(matches) == 1
        assert matches[0].source_url == "grid://series/10"
        assert session.scalar(select(ExternalEntityMap)).local_id == matches[0].id

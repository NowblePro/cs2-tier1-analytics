from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.schema import Base, GridEntityMap, GridStatsSnapshot, Match, Team
from app.repositories.team_aliases import find_team_by_alias, merge_team_aliases


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


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

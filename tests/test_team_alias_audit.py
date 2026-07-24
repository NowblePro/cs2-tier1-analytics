from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.schema import Base
from app.models.schema import ExternalEntityMap, RankingSnapshot, RankingSnapshotTeam, Team
from app.repositories.team_alias_audit import audit_top_team_aliases


class FakePandaScore:
    def search_teams(self, name: str):
        return [{"id": 42, "name": "Team Falcons"}] if name == "Falcons" else []


def test_audit_matches_provider_alias_and_saves_mapping():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        snapshot = RankingSnapshot(ranking_date=datetime(2026, 7, 24), source_url="fixture")
        team = Team(hltv_team_id=100, name="Falcons")
        session.add_all([snapshot, team])
        session.flush()
        session.add(RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=team.id, rank=1, points=100))
        session.commit()

        report = audit_top_team_aliases(session, pandascore_client=FakePandaScore())
        session.commit()

        assert report["matched"] == 1
        mapping = session.query(ExternalEntityMap).one()
        assert mapping.external_id == "42"
        assert mapping.local_id == team.id

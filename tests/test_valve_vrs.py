from datetime import datetime

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.schema import Base, RankingSnapshot, RankingSnapshotTeam, Team
from app.valve_vrs.client import ValveVrsClient
from app.valve_vrs.ingest import ingest_latest_valve_ranking
from app.valve_vrs.parser import parse_valve_ranking


MARKDOWN = """### Standings as of 2026_07_06<br />

| Standing | Points | Team Name | Roster | |
| :- | -: | :- | :- | :- |
| 1 | 1993 | Spirit | donk, sh1ro | [details](one.md) |
| 2 | 1988 | Falcons | NiKo, m0NESY | [details](two.md) |
| 3 | 1908 | Vitality | ZywOo, ropz | [details](three.md) |
"""


def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_parse_valve_ranking_respects_limit():
    ranking = parse_valve_ranking(MARKDOWN, limit=2)
    assert ranking.ranking_date == datetime(2026, 7, 6)
    assert [(team.rank, team.points, team.name) for team in ranking.teams] == [(1, 1993, "Spirit"), (2, 1988, "Falcons")]


def test_client_selects_latest_global_file():
    def handler(request: httpx.Request):
        if "/contents/invitation/2026" in str(request.url):
            return httpx.Response(200, json=[
                {"name": "standings_global_2026_06_01.md", "download_url": "https://raw.test/old", "html_url": "https://github.test/old"},
                {"name": "standings_global_2026_07_06.md", "download_url": "https://raw.test/latest", "html_url": "https://github.test/latest"},
                {"name": "standings_europe_2026_07_06.md", "download_url": "https://raw.test/eu", "html_url": "https://github.test/eu"},
            ])
        if str(request.url) == "https://raw.test/latest":
            return httpx.Response(200, text=MARKDOWN)
        return httpx.Response(404)

    settings = Settings(valve_vrs_github_api_url="https://api.test/repo", max_retries=0)
    with ValveVrsClient(settings, transport=httpx.MockTransport(handler)) as client:
        markdown, source_url = client.fetch_latest_global(2026)
    assert markdown == MARKDOWN
    assert source_url == "https://github.test/latest"


def test_ingest_is_idempotent_and_reuses_existing_team_alias():
    class FakeClient:
        def fetch_latest_global(self):
            return MARKDOWN, "https://github.test/latest"

    Session = session_factory()
    with Session.begin() as session:
        falcons = Team(hltv_team_id=11283, name="Team Falcons")
        session.add(falcons)
        first = ingest_latest_valve_ranking(session, FakeClient(), limit=3)
        second = ingest_latest_valve_ranking(session, FakeClient(), limit=3)
        snapshots = session.scalar(select(func.count()).select_from(RankingSnapshot))
        rows = session.scalar(select(func.count()).select_from(RankingSnapshotTeam))
        valve_falcons = session.scalar(
            select(Team)
            .join(RankingSnapshotTeam, RankingSnapshotTeam.team_id == Team.id)
            .where(RankingSnapshotTeam.rank == 2)
        )
    assert first["created"] is True
    assert second["created"] is False
    assert snapshots == 1
    assert rows == 3
    assert valve_falcons.id == falcons.id

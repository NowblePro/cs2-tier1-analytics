from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.dust2.importer import import_dust2_match
from app.dust2.parser import parse_dust2_match
from app.dust2.resolver import resolve_dust2_match
from app.models.schema import Base, Match, MatchMap, Round, Team


DUST2_HTML = """
<html>
  <head><title>FOKUS vs. Aurora at BLAST Bounty 2026 Season 2</title></head>
  <body>
    <div class="lineup"><img class="team-logo" alt="FOKUS" /></div>
    <div class="lineup"><img class="team-logo" alt="Aurora" /></div>
    <div class="map-container">
      <div class="map-container-map-name">Cache</div>
      <div class="map-container-score-left">13</div>
      <div class="map-container-score-right">11</div>
    </div>
    <div class="round-breakdown-container">
      <div class="round-breakdown-team-wrapper">
        <div class="round-breakdown-team-logo-container"><img alt="FOKUS" /></div>
        <div class="round-breakdown-half">
          <div class="round-breakdown-cell"></div>
          <div class="round-breakdown-cell"><img class="round-breakdown-icon" src="/scoreboard/t_win.svg" title="1 - 1" /></div>
        </div>
        <div class="round-breakdown-half">
          <div class="round-breakdown-cell"><img class="round-breakdown-icon" src="/scoreboard/ct_win.svg" title="7 - 6" /></div>
        </div>
      </div>
      <div class="round-breakdown-team-wrapper">
        <div class="round-breakdown-team-logo-container"><img alt="Aurora" /></div>
        <div class="round-breakdown-half">
          <div class="round-breakdown-cell"><img class="round-breakdown-icon" src="/scoreboard/ct_win.svg" title="0 - 1" /></div>
          <div class="round-breakdown-cell"></div>
        </div>
        <div class="round-breakdown-half">
          <div class="round-breakdown-cell"></div>
        </div>
      </div>
    </div>
  </body>
</html>
"""


def test_parse_dust2_match_maps_and_rounds():
    parsed = parse_dust2_match(DUST2_HTML)

    assert parsed.team1_name == "FOKUS"
    assert parsed.team2_name == "Aurora"
    assert parsed.maps[0].name == "Cache"
    assert parsed.maps[0].score_team1 == 13
    assert parsed.maps[0].score_team2 == 11
    assert [(row.round_number, row.winner_team_name, row.winner_side, row.is_pistol) for row in parsed.rounds] == [
        (1, "Aurora", "CT", True),
        (2, "FOKUS", "T", False),
        (13, "FOKUS", "CT", True),
    ]


def test_import_dust2_match_replaces_rounds_and_updates_map():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session.begin() as session:
        fokus = Team(hltv_team_id=1, name="FOKUS", country=None)
        aurora = Team(hltv_team_id=2, name="Aurora Gaming", country=None)
        session.add_all([fokus, aurora])
        session.flush()
        match = Match(
            hltv_match_id=10,
            match_time=datetime.now(UTC).replace(tzinfo=None),
            status="completed",
            team1_id=fokus.id,
            team2_id=aurora.id,
            source_url="test://match",
        )
        session.add(match)
        session.flush()
        match_id = match.id

    with Session.begin() as session:
        result = import_dust2_match(session, DUST2_HTML, match_id=match_id, url="https://www.dust2.us/matches/1/fokus-vs-aurora")

    with Session() as session:
        match_map = session.scalar(select(MatchMap).where(MatchMap.match_id == match_id))
        rounds = session.scalars(select(Round).order_by(Round.round_number)).all()

    assert result.maps_imported == 1
    assert result.rounds_imported == 3
    assert match_map.name == "Cache"
    assert match_map.score_team1 == 13
    assert match_map.score_team2 == 11
    assert [row.round_number for row in rounds] == [1, 2, 13]
    assert rounds[0].winner_team_id is not None
    assert rounds[0].is_pistol

    engine.dispose()


def test_resolve_dust2_match_finds_candidate_from_results():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    class FakeClient:
        def fetch_results(self):
            return """
            <a href="/matches/2396000/fokus-vs-aurora">FOKUS</a>
            <a href="/matches/1/other-vs-team">Other</a>
            """

        def fetch_match(self, _url):
            return DUST2_HTML

    with Session.begin() as session:
        fokus = Team(hltv_team_id=1, name="FOKUS", country=None)
        aurora = Team(hltv_team_id=2, name="Aurora Gaming", country=None)
        session.add_all([fokus, aurora])
        session.flush()
        match = Match(
            hltv_match_id=10,
            match_time=datetime.now(UTC).replace(tzinfo=None),
            status="completed",
            team1_id=fokus.id,
            team2_id=aurora.id,
            source_url="test://match",
        )
        session.add(match)
        session.flush()
        match_id = match.id

    with Session() as session:
        candidates = resolve_dust2_match(session, FakeClient(), match_id)

    assert len(candidates) == 1
    assert candidates[0].url == "https://www.dust2.us/matches/2396000/fokus-vs-aurora"
    assert candidates[0].score >= 100

    engine.dispose()

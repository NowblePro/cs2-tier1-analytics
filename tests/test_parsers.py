from pathlib import Path

from app.scraping.map_parser import parse_maps
from app.scraping.match_parser import parse_match
from app.scraping.player_stats_parser import calculate_kd_ratio, parse_player_stats
from app.scraping.ranking_parser import parse_ranking
from app.scraping.round_parser import is_pistol_round, parse_rounds

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_ranking():
    dto = parse_ranking(read_fixture("ranking.html"), "https://www.hltv.org/ranking/teams")
    assert len(dto.teams) == 2
    assert dto.teams[0].rank == 1
    assert dto.teams[0].hltv_team_id == 6667
    assert dto.teams[1].points == 900


def test_parse_saved_hltv_ranking_fixture_if_present():
    files = list(FIXTURES.glob("Counter-Strike Ranking*.html"))
    if not files:
        return
    dto = parse_ranking(files[0].read_text(encoding="utf-8", errors="ignore"), "https://www.hltv.org/ranking/teams")
    assert len(dto.teams) == 30
    assert dto.teams[0].rank == 1
    assert dto.teams[0].hltv_team_id
    assert dto.teams[0].name
    assert dto.teams[-1].rank == 30


def test_parse_match():
    dto = parse_match(read_fixture("match.html"), "https://www.hltv.org/matches/123456/test")
    assert dto.hltv_match_id == 123456
    assert dto.team1.name == "FaZe"
    assert dto.winner_hltv_team_id == 6667
    assert dto.best_of == 3


def test_parse_maps():
    maps = parse_maps(read_fixture("match.html"))
    assert len(maps) == 2
    assert maps[0].name == "Inferno"
    assert maps[0].score_team1 == 13
    assert maps[0].score_team2 == 10


def test_parse_player_stats_and_kd():
    stats = parse_player_stats(read_fixture("match.html"))
    assert stats[0].nickname == "rain"
    assert stats[0].kd_ratio == 2.0
    assert calculate_kd_ratio(10, 0) is None


def test_parse_rounds_and_pistols():
    rounds = parse_rounds(read_fixture("match.html"))
    assert [r.round_number for r in rounds if r.is_pistol] == [1, 13]
    assert is_pistol_round(1, False)
    assert not is_pistol_round(1, True)


def test_missing_fields_do_not_crash():
    dto = parse_match('<main data-match-id="1"></main>', "https://www.hltv.org/matches/1/x")
    assert dto.team1 is None
    assert dto.maps == []


def test_parse_saved_hltv_match_fixtures_if_present():
    files = list(FIXTURES.glob("*vs*.html"))
    if not files:
        return
    parsed = [parse_match(path.read_text(encoding="utf-8", errors="ignore"), "https://www.hltv.org/matches/0/x") for path in files]
    assert all(match.hltv_match_id for match in parsed)
    assert all(match.team1 and match.team2 for match in parsed)
    completed = [match for match in parsed if match.status == "completed"]
    assert completed
    assert any(len(match.maps) >= 2 for match in completed)
    assert any(len(match.player_stats) >= 10 for match in completed)

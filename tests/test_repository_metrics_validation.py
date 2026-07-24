from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.metrics import compute_metrics
from app.models.schema import Base, Match, PlayerMapStat, RankingSnapshot, Round, TeamRollingMetric
from app.repositories import AnalyticsRepository
from app.scraping.dto import MatchDTO, MapDTO, TeamDTO
from app.scraping.match_parser import parse_match
from app.scraping.ranking_parser import parse_ranking
from app.validation import validate_data

FIXTURES = Path(__file__).parent / "fixtures"


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_idempotent_match_write_and_metrics():
    Session = session_factory()
    html = (FIXTURES / "match.html").read_text(encoding="utf-8")
    dto = parse_match(html, "https://www.hltv.org/matches/123456/test")
    with Session.begin() as session:
        repo = AnalyticsRepository(session)
        repo.save_match(dto)
        repo.save_match(dto)
    with Session.begin() as session:
        assert len(session.scalars(select(Match)).all()) == 1
        assert len(session.scalars(select(PlayerMapStat)).all()) == 2
        count = compute_metrics(session)
        assert count == 2
        metrics = session.scalars(select(TeamRollingMetric)).all()
        assert len(metrics) == 2
        assert any(metric.kd_ratio == 2.0 for metric in metrics)


def test_match_update_without_rounds_preserves_existing_round_history():
    Session = session_factory()
    html = (FIXTURES / "match.html").read_text(encoding="utf-8")
    dto = parse_match(html, "https://www.hltv.org/matches/123456/test")
    with Session.begin() as session:
        repo = AnalyticsRepository(session)
        repo.save_match(dto)
        update_without_rounds = MatchDTO(
            hltv_match_id=dto.hltv_match_id,
            source_url="grid://series/test",
            match_time=dto.match_time,
            status=dto.status,
            team1=TeamDTO(hltv_team_id=dto.team1.hltv_team_id, name=dto.team1.name, country=dto.team1.country) if dto.team1 else None,
            team2=TeamDTO(hltv_team_id=dto.team2.hltv_team_id, name=dto.team2.name, country=dto.team2.country) if dto.team2 else None,
            winner_hltv_team_id=dto.winner_hltv_team_id,
            event_name=dto.event_name,
            best_of=dto.best_of,
            score_team1=dto.score_team1,
            score_team2=dto.score_team2,
            maps=[
                MapDTO(
                    map_number=item.map_number,
                    name=item.name,
                    hltv_mapstats_id=item.hltv_mapstats_id,
                    score_team1=item.score_team1,
                    score_team2=item.score_team2,
                    winner_hltv_team_id=item.winner_hltv_team_id,
                )
                for item in dto.maps
            ],
            rounds=[],
            player_stats=dto.player_stats,
        )
        repo.save_match(update_without_rounds)
    with Session() as session:
        match = session.scalar(select(Match))
        assert match.source_url == "grid://series/test"
        assert len(session.scalars(select(Match)).all()) == 1
        assert session.scalar(select(func.count(Round.id))) == len(dto.rounds)


def test_ranking_snapshot_is_historical():
    Session = session_factory()
    html = (FIXTURES / "ranking.html").read_text(encoding="utf-8")
    dto = parse_ranking(html, "https://www.hltv.org/ranking/teams")
    with Session.begin() as session:
        repo = AnalyticsRepository(session)
        repo.save_ranking_snapshot(dto)
        repo.save_ranking_snapshot(dto)
    with Session() as session:
        assert len(session.scalars(select(RankingSnapshot)).all()) == 2


def test_validate_data_report(tmp_path):
    Session = session_factory()
    dto = parse_match((FIXTURES / "match.html").read_text(encoding="utf-8"), "https://www.hltv.org/matches/123456/test")
    with Session.begin() as session:
        AnalyticsRepository(session).save_match(dto)
        compute_metrics(session)
    with Session() as session:
        report_path = tmp_path / "validation.json"
        report = validate_data(session, report_path)
    assert report_path.exists()
    assert any(item["code"] == "unknown_map_names" for item in report["issues"])

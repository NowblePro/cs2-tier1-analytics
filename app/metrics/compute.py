from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.schema import Match, MatchMap, PlayerMapStat, Round, Team, TeamRollingMetric


def ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if denominator in (None, 0) or numerator is None:
        return None
    return round(float(numerator) / float(denominator), 4)


def compute_metrics(session: Session) -> int:
    session.execute(delete(TeamRollingMetric))
    teams = session.scalars(select(Team)).all()
    count = 0
    for team in teams:
        matches = session.scalars(select(Match).where((Match.team1_id == team.id) | (Match.team2_id == team.id))).all()
        completed = [m for m in matches if m.status == "completed"]
        won_matches = sum(1 for m in completed if m.winner_team_id == team.id)
        maps = session.scalars(
            select(MatchMap).join(Match, MatchMap.match_id == Match.id).where((Match.team1_id == team.id) | (Match.team2_id == team.id))
        ).all()
        played_maps = [m for m in maps if m.score_team1 is not None and m.score_team2 is not None]
        won_maps = sum(1 for m in played_maps if m.winner_team_id == team.id)
        stats = session.execute(select(func.sum(PlayerMapStat.kills), func.sum(PlayerMapStat.deaths)).where(PlayerMapStat.team_id == team.id)).one()
        rounds = session.scalars(select(Round).where(Round.winner_team_id == team.id)).all()
        team_round_wins = len(rounds)
        all_rounds = session.scalar(
            select(func.count(Round.id)).join(MatchMap, Round.match_map_id == MatchMap.id).join(Match, MatchMap.match_id == Match.id).where((Match.team1_id == team.id) | (Match.team2_id == team.id))
        )
        pistol_played = session.scalar(
            select(func.count(Round.id)).join(MatchMap, Round.match_map_id == MatchMap.id).join(Match, MatchMap.match_id == Match.id).where(Round.is_pistol.is_(True), (Match.team1_id == team.id) | (Match.team2_id == team.id))
        )
        pistol_won = session.scalar(select(func.count(Round.id)).where(Round.is_pistol.is_(True), Round.winner_team_id == team.id))
        session.add(
            TeamRollingMetric(
                team_id=team.id,
                window_name="all",
                matches_played=len(completed),
                match_win_rate=ratio(won_matches, len(completed)),
                map_win_rate=ratio(won_maps, len(played_maps)),
                kd_ratio=ratio(stats[0], stats[1]),
                t_round_win_rate=ratio(sum(1 for r in rounds if r.winner_side == "T"), all_rounds),
                ct_round_win_rate=ratio(sum(1 for r in rounds if r.winner_side == "CT"), all_rounds),
                pistol_win_rate=ratio(pistol_won, pistol_played),
            )
        )
        count += 1
    return count


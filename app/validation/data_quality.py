import json
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.grid.ingest import KNOWN_MAPS
from app.models.schema import Match, MatchMap, Player, PlayerMapStat, Round, TeamRollingMetric


def _issue(code: str, message: str, count: int) -> dict[str, Any]:
    return {"code": code, "message": message, "count": count}


def validate_data(session: Session, output_path: Path | None = None) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    issues.append(_issue("matches_without_two_teams", "Matches without two teams", session.scalar(select(func.count(Match.id)).where((Match.team1_id.is_(None)) | (Match.team2_id.is_(None)))) or 0))
    issues.append(_issue("completed_without_winner", "Completed matches without winner", session.scalar(select(func.count(Match.id)).where(Match.status == "completed", Match.winner_team_id.is_(None))) or 0))
    issues.append(_issue("maps_without_score", "Maps without score", session.scalar(select(func.count(MatchMap.id)).where((MatchMap.score_team1.is_(None)) | (MatchMap.score_team2.is_(None)))) or 0))
    issues.append(_issue("negative_player_stats", "Negative player stat values", session.scalar(select(func.count(PlayerMapStat.id)).where((PlayerMapStat.kills < 0) | (PlayerMapStat.deaths < 0) | (PlayerMapStat.assists < 0))) or 0))
    issues.append(_issue("kd_without_kills_or_deaths", "K/D exists without kills or deaths", session.scalar(select(func.count(PlayerMapStat.id)).where(PlayerMapStat.kd_ratio.is_not(None), (PlayerMapStat.kills.is_(None)) | (PlayerMapStat.deaths.is_(None)))) or 0))
    duplicate_matches = session.execute(select(Match.hltv_match_id, func.count()).group_by(Match.hltv_match_id).having(func.count() > 1)).all()
    issues.append(_issue("duplicate_matches", "Duplicate matches", len(duplicate_matches)))
    duplicate_maps = session.execute(select(MatchMap.match_id, MatchMap.map_number, func.count()).group_by(MatchMap.match_id, MatchMap.map_number).having(func.count() > 1)).all()
    issues.append(_issue("duplicate_maps", "Duplicate maps", len(duplicate_maps)))
    issues.append(_issue("players_without_team", "Players without current team", session.scalar(select(func.count(Player.id)).where(Player.current_team_id.is_(None))) or 0))
    mismatched_scores = 0
    for match_map in session.scalars(select(MatchMap)).all():
        if match_map.score_team1 is None or match_map.score_team2 is None:
            continue
        last_round = session.scalars(select(Round).where(Round.match_map_id == match_map.id).order_by(Round.round_number.desc())).first()
        if last_round and (last_round.score_team1_after != match_map.score_team1 or last_round.score_team2_after != match_map.score_team2):
            mismatched_scores += 1
    issues.append(_issue("round_sum_score_mismatch", "Round history final score differs from map score", mismatched_scores))
    maps_with_rounds_no_pistols = 0
    for _map_id, total_rounds, pistols in session.execute(
        select(Round.match_map_id, func.count(Round.id), func.sum(case((Round.is_pistol.is_(True), 1), else_=0))).group_by(Round.match_map_id)
    ):
        if total_rounds and not pistols:
            maps_with_rounds_no_pistols += 1
    issues.append(_issue("round_history_without_pistols", "Maps with rounds but no detected pistol rounds", maps_with_rounds_no_pistols))
    issues.append(_issue("pistol_win_rate_over_100", "Pistol win rate greater than 100%", session.scalar(select(func.count(TeamRollingMetric.id)).where(TeamRollingMetric.pistol_win_rate > 1)) or 0))
    unknown_maps = session.scalar(select(func.count(MatchMap.id)).where(MatchMap.name.not_in(KNOWN_MAPS))) or 0
    issues.append(_issue("unknown_map_names", "Unknown map names", unknown_maps))
    report = {"ok": all(item["count"] == 0 for item in issues), "issues": issues}
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report

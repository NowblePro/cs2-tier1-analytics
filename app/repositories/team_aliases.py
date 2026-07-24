from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.schema import (
    ExternalEntityMap,
    GridEntityMap,
    GridStatsSnapshot,
    Match,
    MatchMap,
    Player,
    PlayerMapStat,
    RankingSnapshotTeam,
    Round,
    Team,
    TeamRollingMetric,
)


def canonical_team_key(value: str | None) -> str:
    if not value:
        return ""
    key = "".join(ch for ch in value.lower() if ch.isalnum())
    if key.startswith("team") and len(key) > 4:
        key = key[4:]
    if key.endswith("team") and len(key) > 4:
        key = key[:-4]
    aliases = {
        "auroragaming": "aurora",
        "falconsesports": "falcons",
        "futesports": "fut",
        "g2esports": "g2",
        "themongolz": "mongolz",
    }
    return aliases.get(key, key)


def _same_result(left: Match, right: Match) -> bool:
    if left.team1_id == right.team1_id and left.team2_id == right.team2_id:
        return (left.score_team1, left.score_team2) == (right.score_team1, right.score_team2)
    if left.team1_id == right.team2_id and left.team2_id == right.team1_id:
        return (left.score_team1, left.score_team2) == (right.score_team2, right.score_team1)
    return False


def merge_duplicate_provider_matches(session: Session) -> dict[str, int]:
    matches = session.scalars(select(Match).where(Match.match_time.is_not(None)).order_by(Match.match_time, Match.id)).all()
    map_counts = dict(session.execute(select(MatchMap.match_id, func.count(MatchMap.id)).group_by(MatchMap.match_id)).all())
    deleted: set[int] = set()
    merged = 0
    for index, left in enumerate(matches):
        if left.id in deleted:
            continue
        for right in matches[index + 1:]:
            if right.id in deleted or (right.match_time - left.match_time).total_seconds() > 3600:
                break
            if not _same_result(left, right):
                continue
            if not ({left.source_url.split(":", 1)[0], right.source_url.split(":", 1)[0]} >= {"grid", "pandascore"}):
                continue
            left_quality = (map_counts.get(left.id, 0), int(left.source_url.startswith("grid://")))
            right_quality = (map_counts.get(right.id, 0), int(right.source_url.startswith("grid://")))
            keeper, duplicate = (left, right) if left_quality >= right_quality else (right, left)
            if map_counts.get(duplicate.id, 0):
                continue
            for entity_map in session.scalars(
                select(ExternalEntityMap).where(ExternalEntityMap.entity_type == "match", ExternalEntityMap.local_id == duplicate.id)
            ).all():
                entity_map.local_id = keeper.id
            for entity_map in session.scalars(
                select(GridEntityMap).where(GridEntityMap.entity_type == "series", GridEntityMap.local_id == duplicate.id)
            ).all():
                entity_map.local_id = keeper.id
            session.delete(duplicate)
            deleted.add(duplicate.id)
            merged += 1
    return {"merged_matches": merged}


def find_team_by_alias(session: Session, name: str | None) -> Team | None:
    key = canonical_team_key(name)
    if not key:
        return None
    teams = session.scalars(select(Team)).all()
    matches = [team for team in teams if canonical_team_key(team.name) == key]
    if not matches:
        return None
    return sorted(matches, key=_canonical_sort_key)[0]


def _canonical_sort_key(team: Team) -> tuple[int, int, int]:
    has_hltv_id = 0 if team.hltv_team_id > 0 else 1
    has_team_prefix = 1 if canonical_team_key(team.name) != "".join(ch for ch in team.name.lower() if ch.isalnum()) else 0
    return has_hltv_id, has_team_prefix, team.id


def _set_fk(session: Session, model, column_name: str, source_id: int, target_id: int) -> int:
    rows = session.scalars(select(model).where(getattr(model, column_name) == source_id)).all()
    for row in rows:
        setattr(row, column_name, target_id)
    return len(rows)


def merge_team_aliases(session: Session, dry_run: bool = False) -> dict[str, int | list[dict[str, object]]]:
    teams = session.scalars(select(Team)).all()
    groups: dict[str, list[Team]] = {}
    for team in teams:
        groups.setdefault(canonical_team_key(team.name), []).append(team)

    merged: list[dict[str, object]] = []
    updated_refs = 0
    deleted_teams = 0
    for key, group in groups.items():
        unique_group = list({team.id: team for team in group}.values())
        if not key or len(unique_group) < 2:
            continue
        target = sorted(unique_group, key=_canonical_sort_key)[0]
        for source in sorted([team for team in unique_group if team.id != target.id], key=lambda item: item.id):
            merged.append({"source_id": source.id, "source_name": source.name, "target_id": target.id, "target_name": target.name})
            if dry_run:
                continue
            updated_refs += _set_fk(session, Match, "team1_id", source.id, target.id)
            updated_refs += _set_fk(session, Match, "team2_id", source.id, target.id)
            updated_refs += _set_fk(session, Match, "winner_team_id", source.id, target.id)
            updated_refs += _set_fk(session, MatchMap, "picked_by_team_id", source.id, target.id)
            updated_refs += _set_fk(session, MatchMap, "winner_team_id", source.id, target.id)
            updated_refs += _set_fk(session, Round, "winner_team_id", source.id, target.id)
            updated_refs += _set_fk(session, Player, "current_team_id", source.id, target.id)
            updated_refs += _set_fk(session, PlayerMapStat, "team_id", source.id, target.id)
            updated_refs += _set_fk(session, RankingSnapshotTeam, "team_id", source.id, target.id)
            updated_refs += _set_fk(session, GridEntityMap, "local_id", source.id, target.id)
            external_team_maps = session.scalars(
                select(ExternalEntityMap).where(
                    ExternalEntityMap.entity_type == "team",
                    ExternalEntityMap.local_id == source.id,
                )
            ).all()
            for entity_map in external_team_maps:
                entity_map.local_id = target.id
                updated_refs += 1
            updated_refs += _set_fk(session, GridStatsSnapshot, "local_id", source.id, target.id)

            for metric in session.scalars(select(TeamRollingMetric).where(TeamRollingMetric.team_id == source.id)).all():
                existing = session.scalar(
                    select(TeamRollingMetric).where(
                        TeamRollingMetric.team_id == target.id,
                        TeamRollingMetric.window_name == metric.window_name,
                    )
                )
                if existing:
                    session.delete(metric)
                else:
                    metric.team_id = target.id
                    updated_refs += 1
            session.delete(source)
            deleted_teams += 1
    if not dry_run:
        session.flush()
        duplicate_result = merge_duplicate_provider_matches(session)
    else:
        duplicate_result = {"merged_matches": 0}
    return {
        "groups": len(merged),
        "updated_refs": updated_refs,
        "deleted_teams": deleted_teams,
        "merged": merged,
        **duplicate_result,
    }

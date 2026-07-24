from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.grid.ids import stable_negative_id
from app.models.schema import Event, ExternalEntityMap, Match, RankingSnapshot, RankingSnapshotTeam, Team
from app.pandascore.client import PandaScoreClient
from app.repositories.team_aliases import canonical_team_key, find_team_by_alias


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _opponents(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry.get("opponent") or {} for entry in item.get("opponents") or [] if entry.get("opponent")]


def _event_name(item: dict[str, Any]) -> str:
    serie = item.get("serie") or {}
    league = item.get("league") or {}
    tournament = item.get("tournament") or {}
    league_name = league.get("name")
    serie_name = serie.get("full_name") or serie.get("name")
    if league_name and serie_name and canonical_team_key(league_name) not in canonical_team_key(serie_name):
        return f"{league_name} - {serie_name}"
    return serie_name or league_name or tournament.get("name") or "PandaScore"


def _latest_top_keys(session: Session, limit: int) -> set[str]:
    snapshot_id = session.scalar(
        select(RankingSnapshot.id).order_by(desc(RankingSnapshot.id)).limit(1)
    )
    if snapshot_id is None:
        return set()
    rows = session.execute(
        select(Team.name)
        .join(RankingSnapshotTeam, RankingSnapshotTeam.team_id == Team.id)
        .where(RankingSnapshotTeam.snapshot_id == snapshot_id)
        .order_by(RankingSnapshotTeam.rank)
        .limit(limit)
    )
    return {canonical_team_key(name) for (name,) in rows}


def _entity_map(session: Session, entity_type: str, external_id: str) -> ExternalEntityMap | None:
    external_id = str(external_id)
    pending = next(
        (
            row for row in session.new
            if isinstance(row, ExternalEntityMap)
            and row.provider == "pandascore"
            and row.entity_type == entity_type
            and row.external_id == external_id
        ),
        None,
    )
    if pending is not None:
        return pending
    return session.scalar(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == "pandascore",
            ExternalEntityMap.entity_type == entity_type,
            ExternalEntityMap.external_id == external_id,
        )
    )


def _save_map(session: Session, entity_type: str, external_id: str, local_table: str, local_id: int, name: str | None) -> None:
    row = _entity_map(session, entity_type, external_id)
    if row is None:
        row = ExternalEntityMap(provider="pandascore", entity_type=entity_type, external_id=external_id, local_table=local_table, local_id=local_id)
        session.add(row)
    row.local_id = local_id
    row.local_table = local_table
    row.name = name


def _resolve_team(session: Session, payload: dict[str, Any]) -> Team:
    external_id = str(payload["id"])
    mapped = _entity_map(session, "team", external_id)
    if mapped:
        team = session.get(Team, mapped.local_id)
        if team:
            return team
    name = payload.get("name") or f"PandaScore Team {external_id}"
    team = find_team_by_alias(session, name)
    if team is None:
        team = Team(hltv_team_id=stable_negative_id(f"pandascore-team:{external_id}"), name=name, logo_url=payload.get("image_url"))
        session.add(team)
        session.flush()
    elif payload.get("image_url") and not team.logo_url:
        team.logo_url = payload["image_url"]
    _save_map(session, "team", external_id, "teams", team.id, name)
    return team


def _find_matching_match(session: Session, team1: Team, team2: Team, match_time: datetime | None) -> Match | None:
    if match_time is None:
        return None
    return session.scalar(
        select(Match)
        .where(
            Match.match_time.between(match_time - timedelta(hours=4), match_time + timedelta(hours=4)),
            or_(
                (Match.team1_id == team1.id) & (Match.team2_id == team2.id),
                (Match.team1_id == team2.id) & (Match.team2_id == team1.id),
            ),
        )
        .order_by(Match.match_time)
        .limit(1)
    )


def _upsert_match(session: Session, item: dict[str, Any]) -> tuple[Match, bool]:
    external_id = str(item["id"])
    opponents = _opponents(item)
    team1 = _resolve_team(session, opponents[0])
    team2 = _resolve_team(session, opponents[1])
    match_time = _parse_datetime(item.get("scheduled_at") or item.get("begin_at"))
    mapped = _entity_map(session, "match", external_id)
    match = session.get(Match, mapped.local_id) if mapped else None
    if match is None:
        match = _find_matching_match(session, team1, team2, match_time)
    created = match is None
    if match is None:
        match = Match(
            hltv_match_id=stable_negative_id(f"pandascore-match:{external_id}"),
            source_url=f"pandascore://match/{external_id}",
            status="scheduled",
        )
        session.add(match)
        session.flush()

    event_name = _event_name(item)
    event = session.scalar(select(Event).where(Event.name == event_name))
    if event is None:
        event = Event(name=event_name)
        session.add(event)
        session.flush()
    match.match_time = match_time
    match.team1_id = team1.id
    match.team2_id = team2.id
    match.event_id = event.id
    match.best_of = item.get("number_of_games") or match.best_of
    result_by_team = {str(result.get("team_id")): result.get("score") for result in item.get("results") or []}
    match.score_team1 = result_by_team.get(str(opponents[0]["id"]), match.score_team1)
    match.score_team2 = result_by_team.get(str(opponents[1]["id"]), match.score_team2)
    winner_id = str(item.get("winner_id")) if item.get("winner_id") is not None else None
    if winner_id == str(opponents[0]["id"]):
        match.winner_team_id = team1.id
    elif winner_id == str(opponents[1]["id"]):
        match.winner_team_id = team2.id
    incoming_status = item.get("status")
    if incoming_status == "finished":
        match.status = "completed" if match.winner_team_id else "finished_unknown"
    elif incoming_status in {"canceled", "cancelled"}:
        match.status = "cancelled"
    elif incoming_status == "postponed":
        match.status = "postponed"
    elif incoming_status == "running" and match.status != "completed":
        match.status = "live"
    elif match.status not in {"completed", "finished_unknown", "live"}:
        match.status = "scheduled"
    _save_map(session, "match", external_id, "matches", match.id, item.get("name"))
    return match, created


def ingest_upcoming_pandascore_matches(
    session: Session,
    client: PandaScoreClient,
    date_from: datetime,
    date_to: datetime,
    *,
    max_pages: int = 5,
    max_matches: int = 500,
    top_limit: int = 50,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    top_keys = _latest_top_keys(session, top_limit)
    if not top_keys:
        raise RuntimeError(f"No top-{top_limit} ranking snapshot found. Load ranking first.")
    pages = checked = matched = saved = new_matches = updated_matches = skipped = errors = 0
    cancelled = False
    total_available = 0
    for page in range(1, max_pages + 1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        items, total_available = client.list_upcoming(date_from, date_to, page=page, per_page=100)
        pages += 1
        if not items:
            break
        for item in items:
            if should_cancel and should_cancel():
                cancelled = True
                break
            checked += 1
            opponents = _opponents(item)
            if len(opponents) != 2:
                skipped += 1
                continue
            if not any(canonical_team_key(team.get("name")) in top_keys for team in opponents):
                skipped += 1
                continue
            matched += 1
            if not dry_run:
                try:
                    _, created = _upsert_match(session, item)
                    saved += 1
                    new_matches += int(created)
                    updated_matches += int(not created)
                except (KeyError, TypeError, ValueError):
                    errors += 1
                    skipped += 1
            if progress:
                progress({"stage": "pandascore-upcoming", "page": page, "pages_limit": max_pages, "checked": checked, "matched": matched, "saved": saved, "errors": errors, "total_available": total_available})
            if saved >= max_matches:
                break
        if cancelled or saved >= max_matches or page * 100 >= total_available:
            break
    return {
        "provider": "pandascore",
        "pages": pages,
        "total_available": total_available,
        "checked": checked,
        "matched": matched,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "new_matches": new_matches,
        "updated_matches": updated_matches,
        "cancelled": cancelled,
    }


def ingest_past_pandascore_results(
    session: Session,
    client: PandaScoreClient,
    date_from: datetime,
    date_to: datetime,
    *,
    max_pages: int = 10,
    max_matches: int = 500,
    top_limit: int = 50,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    top_keys = _latest_top_keys(session, top_limit)
    if not top_keys:
        raise RuntimeError(f"No top-{top_limit} ranking snapshot found. Load ranking first.")
    pages = checked = matched = saved = new_matches = updated_matches = skipped = errors = 0
    cancelled = False
    total_available = 0
    for page in range(1, max_pages + 1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        items, total_available = client.list_past(date_from, date_to, page=page, per_page=100)
        pages += 1
        if not items:
            break
        for item in items:
            if should_cancel and should_cancel():
                cancelled = True
                break
            checked += 1
            opponents = _opponents(item)
            if len(opponents) != 2:
                skipped += 1
                continue
            already_tracked = _entity_map(session, "match", str(item.get("id"))) is not None
            ranked_match = any(canonical_team_key(team.get("name")) in top_keys for team in opponents)
            if not already_tracked and not ranked_match:
                skipped += 1
                continue
            matched += 1
            if not dry_run:
                try:
                    _, created = _upsert_match(session, item)
                    saved += 1
                    new_matches += int(created)
                    updated_matches += int(not created)
                except (KeyError, TypeError, ValueError):
                    errors += 1
                    skipped += 1
            if progress:
                progress({"stage": "pandascore-results", "page": page, "pages_limit": max_pages, "checked": checked, "matched": matched, "saved": saved, "errors": errors, "total_available": total_available})
            if saved >= max_matches:
                break
        if cancelled or saved >= max_matches or page * 100 >= total_available:
            break
    return {
        "provider": "pandascore",
        "operation": "results",
        "pages": pages,
        "total_available": total_available,
        "checked": checked,
        "matched": matched,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "new_matches": new_matches,
        "updated_matches": updated_matches,
        "cancelled": cancelled,
    }


def ingest_team_pandascore_history(
    session: Session,
    client: PandaScoreClient,
    team: Team,
    date_from: datetime,
    date_to: datetime,
    *,
    max_pages: int = 10,
    max_matches: int = 500,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    mapped = session.scalar(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == "pandascore",
            ExternalEntityMap.entity_type == "team",
            ExternalEntityMap.local_id == team.id,
        )
    )
    if mapped is None:
        candidates = client.search_teams(team.name)
        candidate = next((item for item in candidates if canonical_team_key(item.get("name")) == canonical_team_key(team.name)), None)
        if candidate is None:
            raise RuntimeError(f"Team {team.name} was not found in PandaScore")
        _save_map(session, "team", str(candidate["id"]), "teams", team.id, candidate.get("name"))
        external_id = str(candidate["id"])
    else:
        external_id = mapped.external_id

    pages = checked = saved = new_matches = updated_matches = errors = 0
    cancelled = False
    total_available = 0
    for page in range(1, max_pages + 1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        items, total_available = client.list_past(date_from, date_to, page=page, per_page=100, team_id=external_id)
        pages += 1
        if not items:
            break
        for item in items:
            if should_cancel and should_cancel():
                cancelled = True
                break
            checked += 1
            if len(_opponents(item)) != 2:
                errors += 1
                continue
            if not dry_run:
                try:
                    _, created = _upsert_match(session, item)
                    saved += 1
                    new_matches += int(created)
                    updated_matches += int(not created)
                except (KeyError, TypeError, ValueError):
                    errors += 1
            if progress:
                progress({"stage": "pandascore-team", "page": page, "pages_limit": max_pages, "checked": checked, "saved": saved, "errors": errors, "total_available": total_available})
            if saved >= max_matches:
                break
        if cancelled or saved >= max_matches or page * 100 >= total_available:
            break
    return {
        "provider": "pandascore",
        "operation": "team-history",
        "team": team.name,
        "pages": pages,
        "total_available": total_available,
        "checked": checked,
        "saved": saved,
        "errors": errors,
        "new_matches": new_matches,
        "updated_matches": updated_matches,
        "cancelled": cancelled,
    }


def ingest_upcoming_with_histories(
    session: Session,
    client: PandaScoreClient,
    date_from: datetime,
    date_to: datetime,
    *,
    history_days: int = 180,
    max_pages: int = 5,
    max_matches: int = 500,
    history_max_pages: int = 2,
    history_max_matches: int = 100,
    top_limit: int = 50,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    upcoming = ingest_upcoming_pandascore_matches(
        session,
        client,
        date_from,
        date_to,
        max_pages=max_pages,
        max_matches=max_matches,
        top_limit=top_limit,
        dry_run=dry_run,
        progress=lambda item: progress({"phase": "upcoming", **item}) if progress else None,
        should_cancel=should_cancel,
    )
    if dry_run or upcoming.get("cancelled") or (should_cancel and should_cancel()):
        return {"upcoming": upcoming, "histories": [], "teams": 0, "cancelled": bool(upcoming.get("cancelled"))}

    session.flush()
    matches = session.scalars(
        select(Match)
        .where(
            Match.match_time >= date_from,
            Match.match_time <= date_to,
            Match.status.in_(["scheduled", "live"]),
        )
        .order_by(Match.match_time, Match.id)
    ).all()
    team_ids = sorted({team_id for match in matches for team_id in (match.team1_id, match.team2_id) if team_id})
    histories: list[dict[str, Any]] = []
    history_from = date_from - timedelta(days=max(1, history_days))
    for index, team_id in enumerate(team_ids, start=1):
        if should_cancel and should_cancel():
            return {"upcoming": upcoming, "histories": histories, "teams": len(team_ids), "cancelled": True}
        team = session.get(Team, team_id)
        if team is None:
            continue
        if progress:
            progress({"phase": "participant-history", "stage": "pandascore-team", "team": team.name, "team_index": index, "teams_total": len(team_ids)})
        try:
            history = ingest_team_pandascore_history(
                session,
                client,
                team,
                history_from,
                date_from,
                max_pages=history_max_pages,
                max_matches=history_max_matches,
                dry_run=False,
                progress=lambda item, name=team.name, position=index: progress(
                    {"phase": "participant-history", "team": name, "team_index": position, "teams_total": len(team_ids), **item}
                ) if progress else None,
                should_cancel=should_cancel,
            )
        except RuntimeError as exc:
            history = {"provider": "pandascore", "operation": "team-history", "team": team.name, "saved": 0, "errors": 1, "error": str(exc)}
        histories.append(history)
    return {
        "upcoming": upcoming,
        "histories": histories,
        "teams": len(team_ids),
        "history_saved": sum(int(item.get("saved", 0)) for item in histories),
        "history_errors": sum(int(item.get("errors", 0)) for item in histories),
        "cancelled": bool(should_cancel and should_cancel()),
    }

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from datetime import timedelta
from pathlib import Path

from app.config import get_settings
from app.analytics import estimate_backfill
from app.db import get_session_factory
from app.logging import configure_logging
from app.grid import GridClient, ingest_recent_grid_series, ingest_upcoming_grid_series, refresh_live_grid_matches, run_grid_backfill, run_grid_update_since_cursor
from app.grid.ingest import _series_is_cs2, grid_state_to_match, latest_top_team_names, normalize_name, save_grid_identity_maps, save_raw_grid_state
from app.grid.client import GridApiError, GridSeriesSummary
from app.grid.stats import refresh_grid_stats
from app.jobs import run_post_sync_pipeline
from app.metrics import compute_metrics
from app.models import Base
from app.models.schema import Event, Match, MatchMap, Team
from app.repositories import AnalyticsRepository
from app.repositories.team_aliases import merge_team_aliases
from app.scraping.client import HltvBlockedError, HltvClient
from app.scraping.match_parser import parse_match
from app.scraping.ranking_parser import parse_ranking
from app.validation import validate_data

logger = logging.getLogger(__name__)


def print_json(data: object, *, ensure_ascii: bool = True) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=ensure_ascii))


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="Plan work without network or database writes")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cached HTML and fetch again")
    parser.add_argument("--from-cache", action="store_true", help="Use cached HTML only")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages to request")
    parser.add_argument("--max-matches", type=int, default=None, help="Maximum matches to process")


def init_db(_: argparse.Namespace) -> None:
    settings = get_settings()
    Path("./data").mkdir(exist_ok=True)
    engine = get_session_factory(settings).kw["bind"]
    Base.metadata.create_all(engine)
    print("Database schema initialized")


def scrape_ranking(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = HltvClient(settings, from_cache=args.from_cache, force_refresh=args.force_refresh, dry_run=args.dry_run)
    Session = get_session_factory(settings)
    started = time.monotonic()
    try:
        if args.html_file:
            html = Path(args.html_file).read_text(encoding="utf-8", errors="ignore")
            source_url = "https://www.hltv.org/ranking/teams"
        else:
            result = client.fetch("/ranking/teams")
            html = result.html
            source_url = result.url
        if args.dry_run:
            print("Dry run: would fetch and parse HLTV ranking")
            return
        snapshot = parse_ranking(html, source_url)
        snapshot.teams[:] = snapshot.teams[: settings.top_teams_limit]
        with Session.begin() as session:
            repo = AnalyticsRepository(session)
            repo.save_ranking_snapshot(snapshot)
        print(f"Saved ranking snapshot with {len(snapshot.teams)} teams")
    except HltvBlockedError as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("scrape-ranking pages=%s skipped=%s blocked=%s duration=%.2fs", client.pages_requested, client.skipped_pages, client.blocked_count, time.monotonic() - started)


def scrape_match(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = HltvClient(settings, from_cache=args.from_cache, force_refresh=args.force_refresh, dry_run=args.dry_run)
    Session = get_session_factory(settings)
    started = time.monotonic()
    try:
        if args.html_file:
            html = Path(args.html_file).read_text(encoding="utf-8", errors="ignore")
            source_url = f"https://www.hltv.org/matches/{args.match_id}/_"
        else:
            result = client.fetch(f"/matches/{args.match_id}/_")
            html = result.html
            source_url = result.url
        if args.dry_run:
            print(f"Dry run: would fetch and save match {args.match_id}")
            return
        dto = parse_match(html, source_url, match_id=args.match_id)
        with Session.begin() as session:
            repo = AnalyticsRepository(session)
            repo.save_match(dto)
            print(f"Saved match {args.match_id}: new={repo.new_matches} updated={repo.updated_matches}")
    except HltvBlockedError as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("scrape-match pages=%s skipped=%s blocked=%s duration=%.2fs", client.pages_requested, client.skipped_pages, client.blocked_count, time.monotonic() - started)


def scrape_recent(args: argparse.Namespace) -> None:
    max_matches = args.max_matches or get_settings().max_matches_per_run
    print(f"scrape-recent placeholder: use scrape-match for vertical slice; would process up to {max_matches} matches for {args.days} days")


def grid_sync_recent(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    date_to = datetime.now(UTC).replace(tzinfo=None)
    date_from = date_to - timedelta(days=args.days)
    max_pages = args.max_pages or 5
    max_matches = args.max_matches or settings.max_matches_per_run
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session.begin() as session:
            result = ingest_recent_grid_series(
                session=session,
                client=client,
                date_from=date_from,
                date_to=date_to,
                max_pages=max_pages,
                max_matches=max_matches,
                dry_run=args.dry_run,
                top_limit=args.top_limit,
                require_top_team=not args.no_top_filter,
            )
        print(json.dumps(result, indent=2))
    except GridApiError as exc:
        logger.error("%s", exc)
        print(str(exc))
    except RuntimeError as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("grid-sync-recent duration=%.2fs", time.monotonic() - started)


def _parse_cli_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def grid_backfill(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    date_to = _parse_cli_datetime(args.date_to) if args.date_to else datetime.now(UTC).replace(tzinfo=None)
    date_from = _parse_cli_datetime(args.date_from) if args.date_from else date_to - timedelta(days=args.days)
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session.begin() as session:
            result = run_grid_backfill(
                session=session,
                client=client,
                date_from=date_from,
                date_to=date_to,
                cursor_name=args.cursor,
                window_days=args.window_days,
                max_pages_per_window=args.max_pages,
                max_matches_per_window=args.max_matches,
                top_limit=args.top_limit,
                require_top_team=not args.no_top_filter,
                dry_run=args.dry_run,
                resume=not args.no_resume,
                progress=lambda item: print(json.dumps({"progress": item}, ensure_ascii=False)),
            )
            if not args.dry_run:
                compute_metrics(session)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (GridApiError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("grid-backfill duration=%.2fs", time.monotonic() - started)


def grid_update(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session.begin() as session:
            result = run_grid_update_since_cursor(
                session=session,
                client=client,
                cursor_name=args.cursor,
                fallback_days=args.fallback_days,
                max_pages=args.max_pages,
                max_matches=args.max_matches,
                top_limit=args.top_limit,
                require_top_team=not args.no_top_filter,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                compute_metrics(session)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (GridApiError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("grid-update duration=%.2fs", time.monotonic() - started)


def grid_sync_upcoming(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    date_from = _parse_cli_datetime(args.date_from) if args.date_from else datetime.now(UTC).replace(tzinfo=None)
    date_to = _parse_cli_datetime(args.date_to) if args.date_to else date_from + timedelta(days=args.days)
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session.begin() as session:
            result = ingest_upcoming_grid_series(
                session=session,
                client=client,
                date_from=date_from,
                date_to=date_to,
                max_pages=args.max_pages,
                max_matches=args.max_matches,
                top_limit=args.top_limit,
                dry_run=args.dry_run,
                history_days=args.history_days,
                history_max_pages=args.history_max_pages,
                history_max_matches=args.history_max_matches,
            )
            if not args.dry_run:
                compute_metrics(session)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (GridApiError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("grid-sync-upcoming duration=%.2fs", time.monotonic() - started)


def grid_probe_upcoming(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session() as session:
            top_names = latest_top_team_names(session, args.top_limit)
        now = datetime.now(UTC).replace(tzinfo=None)
        schema = {}
        for type_name in ["SeriesFilter", "SeriesOrderBy", "SeriesWorkflowStatus", "OrderDirection"]:
            type_info = client.schema_type_info("central", type_name) or {}
            schema[type_name] = {
                "kind": type_info.get("kind"),
                "fields": [
                    {
                        "name": field.get("name"),
                        "type": _format_graphql_type(field.get("type") or {}),
                        **({"defaultValue": field.get("defaultValue")} if field.get("defaultValue") is not None else {}),
                    }
                    for field in (type_info.get("inputFields") or type_info.get("fields") or [])
                ],
                "values": [value.get("name") for value in (type_info.get("enumValues") or [])],
            }
        windows = []
        for days in args.windows:
            date_to = now + timedelta(days=days)
            after = None
            pages = 0
            items = []
            while pages < args.max_pages:
                summaries, page_info = client.list_series(now, date_to, first=args.first, after=after, order_direction="ASC")
                pages += 1
                for item in summaries:
                    teams = [(team.get("baseInfo") or {}).get("name") for team in item.teams]
                    normalized = [normalize_name(name or "") for name in teams]
                    is_cs2 = _series_is_cs2(item)
                    matches_top = any(name in top_names for name in normalized)
                    items.append(
                        {
                            "id": item.id,
                            "startTimeScheduled": item.start_time_scheduled,
                            "workflowStatus": item.workflow_status,
                            "titleName": item.title_name,
                            "tournamentName": item.tournament_name,
                            "teams": teams,
                            "isCs2": is_cs2,
                            "matchesTopFilter": matches_top,
                        }
                    )
                    if len(items) >= args.limit:
                        break
                if len(items) >= args.limit or not page_info.get("hasNextPage"):
                    break
                after = page_info.get("endCursor")
            windows.append(
                {
                    "days": days,
                    "pages": pages,
                    "count": len(items),
                    "cs2_count": sum(1 for item in items if item["isCs2"]),
                    "top_filter_count": sum(1 for item in items if item["matchesTopFilter"]),
                    "items": items,
                }
            )
        report = {"top_limit": args.top_limit, "top_snapshot_loaded": bool(top_names), "schema": schema, "windows": windows}
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            report = {**report, "saved_to": str(output)}
        print_json(report)
    except GridApiError as exc:
        print(str(exc))
    finally:
        client.close()


def _grid_series_id_from_source(source_url: str) -> str | None:
    prefix = "grid://series/"
    if source_url.startswith(prefix):
        return source_url[len(prefix):]
    return None


def grid_refresh_saved(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    refreshed = skipped = errors = 0
    try:
        with Session.begin() as session:
            repo = AnalyticsRepository(session)
            matches = (
                session.query(Match)
                .filter(Match.source_url.like("grid://series/%"))
                .order_by(Match.match_time.desc(), Match.id.desc())
                .limit(args.limit)
                .all()
            )
            for match in matches:
                series_id = _grid_series_id_from_source(match.source_url)
                team1 = session.get(Team, match.team1_id) if match.team1_id else None
                team2 = session.get(Team, match.team2_id) if match.team2_id else None
                event = session.get(Event, match.event_id) if match.event_id else None
                if not series_id or not team1 or not team2:
                    skipped += 1
                    continue
                try:
                    state = client.series_state(series_id)
                    save_raw_grid_state(session, series_id, state)
                    summary = GridSeriesSummary(
                        id=series_id,
                        start_time_scheduled=match.match_time.strftime("%Y-%m-%dT%H:%M:%SZ") if match.match_time else None,
                        tournament_name=event.name if event else None,
                        title_name="Counter Strike 2",
                        teams=[
                            {"baseInfo": {"id": f"local-{team1.id}", "name": team1.name}},
                            {"baseInfo": {"id": f"local-{team2.id}", "name": team2.name}},
                        ],
                    )
                    dto = grid_state_to_match(summary, state, session)
                    if dto is None:
                        skipped += 1
                        continue
                    repo.save_match(dto)
                    save_grid_identity_maps(session, summary, state, dto)
                    refreshed += 1
                    print(json.dumps({"refreshed": refreshed, "series_id": series_id}, ensure_ascii=False))
                except GridApiError as exc:
                    logger.warning("Skipping GRID refresh %s: %s", series_id, exc)
                    errors += 1
            if not args.no_metrics:
                compute_metrics(session)
        print(json.dumps({"refreshed": refreshed, "skipped": skipped, "errors": errors}, indent=2, ensure_ascii=False))
    finally:
        client.close()
        logger.info("grid-refresh-saved duration=%.2fs", time.monotonic() - started)


def grid_refresh_live(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session.begin() as session:
            result = refresh_live_grid_matches(session=session, client=client, limit=args.limit, dry_run=args.dry_run)
            if not args.dry_run and not args.no_metrics:
                compute_metrics(session)
        print(json.dumps(result, indent=2, ensure_ascii=True))
    finally:
        client.close()
        logger.info("grid-refresh-live duration=%.2fs", time.monotonic() - started)


def grid_scan_series(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    date_to = _parse_cli_datetime(args.date_to) if args.date_to else datetime.now(UTC).replace(tzinfo=None)
    date_from = _parse_cli_datetime(args.date_from) if args.date_from else date_to - timedelta(days=args.days)
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session() as session:
            top_names = latest_top_team_names(session, args.top_limit)
        after = None
        rows = []
        pages = 0
        while pages < args.max_pages and len(rows) < args.limit:
            summaries, page_info = client.list_series(date_from, date_to, first=50, after=after)
            pages += 1
            for item in summaries:
                teams = [(team.get("baseInfo") or {}).get("name") for team in item.teams]
                normalized = [normalize_name(name or "") for name in teams]
                rows.append(
                    {
                        "id": item.id,
                        "startTimeScheduled": item.start_time_scheduled,
                        "titleName": item.title_name,
                        "tournamentName": item.tournament_name,
                        "teams": teams,
                        "matchesTopFilter": any(name in top_names for name in normalized),
                    }
                )
                if len(rows) >= args.limit:
                    break
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
        print(json.dumps({"pages": pages, "count": len(rows), "items": rows}, indent=2, ensure_ascii=False))
    except GridApiError as exc:
        print(str(exc))
    finally:
        client.close()


def grid_stats_schema_report(args: argparse.Namespace) -> None:
    settings = get_settings()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    output = Path(args.output)
    type_names = [
        "Query",
        "TeamStatisticsFilter",
        "PlayerStatisticsFilter",
        "SeriesStatisticsFilter",
        "GameStatisticsFilter",
        "GameSelection",
        "GameStateFilter",
        "DateTimeFilter",
        "TimeRangeFilter",
        "IdFilter",
        "GameOrder",
        "GameOrderField",
        "OrderDirection",
        "TeamStatistics",
        "PlayerStatistics",
        "SeriesStatistics",
        "GameStatistics",
        "TeamStatisticsTournamentFilter",
        "PlayerStatisticsTournamentFilter",
        "SeriesStatisticsTournamentFilter",
        "GameStatisticsTournamentFilter",
        "TeamGameStatisticsTournamentFilter",
        "MapFilter",
        "GameTeamStateFilter",
        "TitleVersionFilter",
        "SegmentStateFilter",
        "GameStatisticsVersionFilter",
        "AggregateIntStatistic",
        "DurationStatistic",
        "BooleanOccurrenceStatistic",
        "Cs2PlayerSeriesStatistics",
        "Cs2TeamSeriesStatistics",
        "PlayerGameStatisticsCs2",
        "TeamGameStatisticsCs2",
        "TeamSegmentStatisticsCs2",
        "PlayerGameStatisticsByTeam",
    ]
    report: dict[str, object] = {}
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        for type_name in type_names:
            report[type_name] = client.query_type_fields("stats", type_name)
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved GRID stats schema report to {output}")
    except Exception as exc:
        output.parent.mkdir(parents=True, exist_ok=True)
        report["error"] = str(exc)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved partial GRID stats schema report to {output}")
        print(str(exc))
    finally:
        client.close()


def grid_stats_refresh(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    started = time.monotonic()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        with Session.begin() as session:
            result = refresh_grid_stats(
                session=session,
                client=client,
                entity_type=args.entity_type,
                window_name=args.window,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (GridApiError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        print(str(exc))
    finally:
        client.close()
        logger.info("grid-stats-refresh duration=%.2fs", time.monotonic() - started)


def backfill(args: argparse.Namespace) -> None:
    max_matches = args.max_matches or get_settings().max_matches_per_run
    print(f"backfill placeholder: would process {args.date_from}..{args.date_to}, max_matches={max_matches}")


def run_compute_metrics(_: argparse.Namespace) -> None:
    Session = get_session_factory()
    with Session.begin() as session:
        count = compute_metrics(session)
    print(f"Computed metrics for {count} teams")


def run_validate_data(args: argparse.Namespace) -> None:
    Session = get_session_factory()
    output = Path(args.output)
    with Session() as session:
        report = validate_data(session, output)
    print(json.dumps(report, indent=2))
    print(f"Saved validation report to {output}")


def normalize_grid_maps(_: argparse.Namespace) -> None:
    Session = get_session_factory()
    with Session.begin() as session:
        maps = (
            session.query(MatchMap)
            .join(Match, MatchMap.match_id == Match.id)
            .filter(Match.source_url.like("grid://%"), MatchMap.name.like("GRID Game%"))
            .all()
        )
        for match_map in maps:
            match_map.name = "GRID Unknown"
        print(f"Normalized {len(maps)} GRID map names")


def delete_non_cs2_grid_matches(_: argparse.Namespace) -> None:
    Session = get_session_factory()
    with Session.begin() as session:
        matches = (
            session.query(Match)
            .outerjoin(Event, Match.event_id == Event.id)
            .filter(Match.source_url.like("grid://%"))
            .filter(Event.name.ilike("%Dota%"))
            .all()
        )
        count = len(matches)
        for match in matches:
            session.delete(match)
        print(f"Deleted {count} non-CS2 GRID matches")


def normalize_team_aliases(args: argparse.Namespace) -> None:
    Session = get_session_factory()
    with Session.begin() as session:
        result = merge_team_aliases(session, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def run_pipeline(args: argparse.Namespace) -> None:
    settings = get_settings()
    Session = get_session_factory(settings)
    Base.metadata.create_all(Session.kw["bind"])
    client = None
    if not args.no_stats:
        try:
            client = GridClient(settings)
        except GridApiError as exc:
            print(str(exc))
            print("Continuing without Stats Feed refresh")
    try:
        with Session.begin() as session:
            result = run_post_sync_pipeline(
                session,
                client,
                stats_window=args.stats_window,
                stats_limit=args.stats_limit,
                refresh_stats_enabled=not args.no_stats and client is not None,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        if client:
            client.close()


def estimate_grid_backfill(args: argparse.Namespace) -> None:
    settings = get_settings()
    result = estimate_backfill(
        days=args.days,
        window_days=args.window_days,
        max_pages=args.max_pages,
        max_matches=args.max_matches,
        request_limit_per_minute=settings.grid_request_limit_per_minute,
        stats_limit=settings.grid_stats_request_limit_per_minute,
        refresh_stats=not args.no_stats,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _format_graphql_type(type_info: dict) -> str:
    kind = type_info.get("kind")
    name = type_info.get("name")
    of_type = type_info.get("ofType")
    if kind == "NON_NULL" and of_type:
        return f"{_format_graphql_type(of_type)}!"
    if kind == "LIST" and of_type:
        return f"[{_format_graphql_type(of_type)}]"
    return name or kind or "Unknown"


def grid_inspect_schema(args: argparse.Namespace) -> None:
    settings = get_settings()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    try:
        if args.types:
            names = client.schema_type_names(args.endpoint, args.contains)
            print(json.dumps(names[: args.limit], indent=2))
            return
        fields = client.query_type_fields(args.endpoint, args.type_name)
        rows = []
        for field in fields[: args.limit]:
            row = {"name": field["name"]}
            if field.get("type"):
                row["type"] = _format_graphql_type(field["type"])
            if field.get("args") is not None:
                row["args"] = {arg["name"]: _format_graphql_type(arg["type"]) for arg in field.get("args") or []}
            if field.get("defaultValue") is not None:
                row["defaultValue"] = field.get("defaultValue")
            rows.append(row)
        print(json.dumps(rows, indent=2))
    except GridApiError as exc:
        print(str(exc))
    finally:
        client.close()


def grid_api_report(args: argparse.Namespace) -> None:
    settings = get_settings()
    try:
        client = GridClient(settings)
    except GridApiError as exc:
        print(str(exc))
        return
    output = Path(args.output)
    date_to = datetime.now(UTC).replace(tzinfo=None)
    date_from = date_to - timedelta(days=args.days)
    keywords = ["series", "game", "team", "player", "map", "round", "event", "tournament"]
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
        },
        "endpoints": {},
        "samples": {},
    }
    try:
        endpoints: dict[str, object] = {}
        for endpoint in ["central", "state"]:
            raw_endpoint_report = client.schema_report(endpoint, keywords, args.types_per_keyword)
            endpoint_report: dict[str, object] = {
                "query_fields": [
                    {
                        "name": field["name"],
                        "type": _format_graphql_type(field["type"]),
                        "args": {arg["name"]: _format_graphql_type(arg["type"]) for arg in field.get("args") or []},
                    }
                    for field in raw_endpoint_report["query_fields"]
                ],
                "types_by_keyword": raw_endpoint_report["types_by_keyword"],
                "type_fields": {
                    name: [
                        {
                            "name": field["name"],
                            "type": _format_graphql_type(field["type"]),
                            "args": {arg["name"]: _format_graphql_type(arg["type"]) for arg in field.get("args") or []},
                        }
                        for field in fields
                    ]
                    for name, fields in raw_endpoint_report["type_fields"].items()
                },
            }
            endpoints[endpoint] = endpoint_report
            time.sleep(args.request_delay)
        report["endpoints"] = endpoints

        sample_series, page_info = client.list_series(date_from, date_to, first=args.sample_series)
        time.sleep(args.request_delay)
        report["samples"] = {
            "allSeries": {
                "count": len(sample_series),
                "pageInfo": page_info,
                "items": [
                    {
                        "id": item.id,
                        "startTimeScheduled": item.start_time_scheduled,
                        "tournamentName": item.tournament_name,
                        "titleName": item.title_name,
                        "teams": item.teams,
                    }
                    for item in sample_series
                ],
            },
            "seriesState": [],
        }
        state_samples = report["samples"]["seriesState"]  # type: ignore[index]
        for item in sample_series[: args.sample_states]:
            try:
                state = client.series_state(item.id)
                time.sleep(args.request_delay)
                state_samples.append(
                    {
                        "seriesId": item.id,
                        "ok": True,
                        "topLevelKeys": sorted(state.keys()),
                        "teamKeys": sorted((state.get("teams") or [{}])[0].keys()) if state.get("teams") else [],
                        "gameKeys": sorted((state.get("games") or [{}])[0].keys()) if state.get("games") else [],
                        "firstGameTeamKeys": sorted(((state.get("games") or [{}])[0].get("teams") or [{}])[0].keys()) if state.get("games") else [],
                        "state": state,
                    }
                )
            except GridApiError as exc:
                state_samples.append({"seriesId": item.id, "ok": False, "error": str(exc)})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved GRID API report to {output}")
    except GridApiError as exc:
        output.parent.mkdir(parents=True, exist_ok=True)
        report["error"] = str(exc)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved partial GRID API report to {output}")
        print(str(exc))
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db")
    init.set_defaults(func=init_db)

    ranking = sub.add_parser("scrape-ranking")
    add_common_flags(ranking)
    ranking.add_argument("--html-file", default=None, help="Parse a saved ranking HTML file instead of fetching")
    ranking.set_defaults(func=scrape_ranking)

    match = sub.add_parser("scrape-match")
    add_common_flags(match)
    match.add_argument("--match-id", type=int, required=True)
    match.add_argument("--html-file", default=None, help="Parse a saved match HTML file instead of fetching")
    match.set_defaults(func=scrape_match)

    recent = sub.add_parser("scrape-recent")
    add_common_flags(recent)
    recent.add_argument("--days", type=int, default=7)
    recent.set_defaults(func=scrape_recent)

    grid_recent = sub.add_parser("grid-sync-recent")
    grid_recent.add_argument("--days", type=int, default=7)
    grid_recent.add_argument("--dry-run", action="store_true")
    grid_recent.add_argument("--max-pages", type=int, default=None)
    grid_recent.add_argument("--max-matches", type=int, default=None)
    grid_recent.add_argument("--top-limit", type=int, default=30)
    grid_recent.add_argument("--no-top-filter", action="store_true")
    grid_recent.set_defaults(func=grid_sync_recent)

    grid_backfill_parser = sub.add_parser("grid-backfill")
    grid_backfill_parser.add_argument("--days", type=int, default=30)
    grid_backfill_parser.add_argument("--from", dest="date_from", default=None)
    grid_backfill_parser.add_argument("--to", dest="date_to", default=None)
    grid_backfill_parser.add_argument("--window-days", type=int, default=1)
    grid_backfill_parser.add_argument("--max-pages", type=int, default=20)
    grid_backfill_parser.add_argument("--max-matches", type=int, default=500)
    grid_backfill_parser.add_argument("--top-limit", type=int, default=50)
    grid_backfill_parser.add_argument("--no-top-filter", action="store_true")
    grid_backfill_parser.add_argument("--cursor", default="grid-main")
    grid_backfill_parser.add_argument("--dry-run", action="store_true")
    grid_backfill_parser.add_argument("--no-resume", action="store_true")
    grid_backfill_parser.set_defaults(func=grid_backfill)

    grid_update_parser = sub.add_parser("grid-update")
    grid_update_parser.add_argument("--fallback-days", type=int, default=7)
    grid_update_parser.add_argument("--max-pages", type=int, default=20)
    grid_update_parser.add_argument("--max-matches", type=int, default=500)
    grid_update_parser.add_argument("--top-limit", type=int, default=50)
    grid_update_parser.add_argument("--no-top-filter", action="store_true")
    grid_update_parser.add_argument("--cursor", default="grid-main")
    grid_update_parser.add_argument("--dry-run", action="store_true")
    grid_update_parser.set_defaults(func=grid_update)

    grid_upcoming_parser = sub.add_parser("grid-sync-upcoming")
    grid_upcoming_parser.add_argument("--days", type=int, default=14)
    grid_upcoming_parser.add_argument("--from", dest="date_from", default=None)
    grid_upcoming_parser.add_argument("--to", dest="date_to", default=None)
    grid_upcoming_parser.add_argument("--max-pages", type=int, default=20)
    grid_upcoming_parser.add_argument("--max-matches", type=int, default=100)
    grid_upcoming_parser.add_argument("--top-limit", type=int, default=50)
    grid_upcoming_parser.add_argument("--history-days", type=int, default=90)
    grid_upcoming_parser.add_argument("--history-max-pages", type=int, default=20)
    grid_upcoming_parser.add_argument("--history-max-matches", type=int, default=200)
    grid_upcoming_parser.add_argument("--dry-run", action="store_true")
    grid_upcoming_parser.set_defaults(func=grid_sync_upcoming)

    grid_probe_parser = sub.add_parser("grid-probe-upcoming")
    grid_probe_parser.add_argument("--top-limit", type=int, default=50)
    grid_probe_parser.add_argument("--windows", type=int, nargs="+", default=[14, 30, 90])
    grid_probe_parser.add_argument("--max-pages", type=int, default=2)
    grid_probe_parser.add_argument("--first", type=int, default=50)
    grid_probe_parser.add_argument("--limit", type=int, default=60)
    grid_probe_parser.add_argument("--output", default=f"data/reports/grid-upcoming-probe-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json")
    grid_probe_parser.set_defaults(func=grid_probe_upcoming)

    grid_refresh_parser = sub.add_parser("grid-refresh-saved")
    grid_refresh_parser.add_argument("--limit", type=int, default=30)
    grid_refresh_parser.add_argument("--no-metrics", action="store_true")
    grid_refresh_parser.set_defaults(func=grid_refresh_saved)

    grid_refresh_live_parser = sub.add_parser("grid-refresh-live")
    grid_refresh_live_parser.add_argument("--limit", type=int, default=50)
    grid_refresh_live_parser.add_argument("--dry-run", action="store_true")
    grid_refresh_live_parser.add_argument("--no-metrics", action="store_true")
    grid_refresh_live_parser.set_defaults(func=grid_refresh_live)

    grid_scan_parser = sub.add_parser("grid-scan-series")
    grid_scan_parser.add_argument("--days", type=int, default=7)
    grid_scan_parser.add_argument("--from", dest="date_from", default=None)
    grid_scan_parser.add_argument("--to", dest="date_to", default=None)
    grid_scan_parser.add_argument("--max-pages", type=int, default=3)
    grid_scan_parser.add_argument("--limit", type=int, default=50)
    grid_scan_parser.add_argument("--top-limit", type=int, default=50)
    grid_scan_parser.set_defaults(func=grid_scan_series)

    grid_stats_schema = sub.add_parser("grid-stats-schema-report")
    grid_stats_schema.add_argument("--output", default=f"data/reports/grid-stats-schema-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json")
    grid_stats_schema.set_defaults(func=grid_stats_schema_report)

    grid_stats_refresh_parser = sub.add_parser("grid-stats-refresh")
    grid_stats_refresh_parser.add_argument("--entity-type", choices=["team", "player"], default="team")
    grid_stats_refresh_parser.add_argument("--window", choices=["LAST_WEEK", "LAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS", "LAST_YEAR"], default="LAST_MONTH")
    grid_stats_refresh_parser.add_argument("--limit", type=int, default=30)
    grid_stats_refresh_parser.add_argument("--dry-run", action="store_true")
    grid_stats_refresh_parser.set_defaults(func=grid_stats_refresh)

    fill = sub.add_parser("backfill")
    add_common_flags(fill)
    fill.add_argument("--from", dest="date_from", required=True)
    fill.add_argument("--to", dest="date_to", required=True)
    fill.set_defaults(func=backfill)

    metrics = sub.add_parser("compute-metrics")
    metrics.set_defaults(func=run_compute_metrics)

    validate = sub.add_parser("validate-data")
    validate.add_argument("--output", default=f"data/reports/validation-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json")
    validate.set_defaults(func=run_validate_data)

    normalize = sub.add_parser("normalize-grid-maps")
    normalize.set_defaults(func=normalize_grid_maps)

    delete_non_cs2 = sub.add_parser("delete-non-cs2-grid-matches")
    delete_non_cs2.set_defaults(func=delete_non_cs2_grid_matches)

    normalize_teams = sub.add_parser("normalize-team-aliases")
    normalize_teams.add_argument("--dry-run", action="store_true")
    normalize_teams.set_defaults(func=normalize_team_aliases)

    pipeline = sub.add_parser("run-pipeline")
    pipeline.add_argument("--stats-window", choices=["LAST_WEEK", "LAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS", "LAST_YEAR"], default="LAST_MONTH")
    pipeline.add_argument("--stats-limit", type=int, default=50)
    pipeline.add_argument("--no-stats", action="store_true")
    pipeline.set_defaults(func=run_pipeline)

    estimate_parser = sub.add_parser("estimate-backfill")
    estimate_parser.add_argument("--days", type=int, default=30)
    estimate_parser.add_argument("--window-days", type=int, default=1)
    estimate_parser.add_argument("--max-pages", type=int, default=20)
    estimate_parser.add_argument("--max-matches", type=int, default=500)
    estimate_parser.add_argument("--no-stats", action="store_true")
    estimate_parser.set_defaults(func=estimate_grid_backfill)

    inspect = sub.add_parser("grid-inspect-schema")
    inspect.add_argument("--endpoint", choices=["central", "state", "stats"], default="central")
    inspect.add_argument("--type-name", default="Query")
    inspect.add_argument("--types", action="store_true", help="List schema type names instead of fields")
    inspect.add_argument("--contains", default=None, help="Filter type names when --types is used")
    inspect.add_argument("--limit", type=int, default=80)
    inspect.set_defaults(func=grid_inspect_schema)

    grid_report = sub.add_parser("grid-api-report")
    grid_report.add_argument("--days", type=int, default=7)
    grid_report.add_argument("--sample-series", type=int, default=5)
    grid_report.add_argument("--sample-states", type=int, default=3)
    grid_report.add_argument("--types-per-keyword", type=int, default=12)
    grid_report.add_argument("--request-delay", type=float, default=1.0)
    grid_report.add_argument("--output", default=f"data/reports/grid-api-report-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json")
    grid_report.set_defaults(func=grid_api_report)
    return parser


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

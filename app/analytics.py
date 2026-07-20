from __future__ import annotations

from datetime import datetime
from typing import Any


def _true_percentage(rows: list[dict[str, Any]] | None) -> float | None:
    for row in rows or []:
        if row.get("value") is True:
            return row.get("percentage")
    return None


def _ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(float(numerator) / float(denominator), 4)


def grid_stats_summary(grid_stats: dict[str, Any] | None) -> dict[str, Any]:
    stats = (grid_stats or {}).get("stats") or {}
    series = stats.get("series") or {}
    game = stats.get("game") or {}
    segments = stats.get("segment") or []
    kills = (series.get("kills") or game.get("kills") or {}).get("sum")
    deaths = (series.get("deaths") or game.get("deaths") or {}).get("sum")
    summary = {
        "series_count": series.get("count"),
        "game_count": game.get("count"),
        "series_win_rate": _true_percentage(series.get("won")),
        "game_win_rate": _true_percentage(game.get("won")),
        "kd_ratio": _ratio(kills, deaths),
        "headshots": (series.get("headshots") or {}).get("sum"),
        "first_kill_rate": None,
        "segments": [],
    }
    normalized_segments = []
    for segment in segments:
        item = {
            "type": segment.get("type"),
            "count": segment.get("count"),
            "win_rate": _true_percentage(segment.get("won")),
            "first_kill_rate": _true_percentage(segment.get("firstKill")),
            "won_first_rate": _true_percentage(segment.get("wonFirst")),
            "kd_ratio": _ratio((segment.get("kills") or {}).get("sum"), (segment.get("deaths") or {}).get("sum")),
        }
        normalized_segments.append(item)
    summary["segments"] = normalized_segments
    first_kill_rates = [item["first_kill_rate"] for item in normalized_segments if item["first_kill_rate"] is not None]
    if first_kill_rates:
        summary["first_kill_rate"] = round(sum(first_kill_rates) / len(first_kill_rates), 2)
    return summary


def _score_component(value: float | None, baseline: float, scale: float, cap: float) -> float:
    if value is None:
        return 0.0
    return max(-cap, min(cap, (value - baseline) * scale))


def team_score(metrics: dict[str, Any], grid_summary: dict[str, Any]) -> tuple[float, list[str]]:
    score = 50.0
    notes: list[str] = []
    score += _score_component(metrics.get("match_win_rate"), 0.5, 18, 10)
    score += _score_component(metrics.get("map_win_rate"), 0.5, 14, 8)
    score += _score_component(metrics.get("kd_ratio"), 1.0, 16, 8)
    score += _score_component(grid_summary.get("series_win_rate"), 50.0, 0.12, 8)
    score += _score_component(grid_summary.get("kd_ratio"), 1.0, 12, 6)
    score += _score_component(grid_summary.get("first_kill_rate"), 50.0, 0.08, 5)
    volume = (metrics.get("matches_played") or 0) + (grid_summary.get("series_count") or 0)
    confidence = min(1.0, volume / 40.0)
    if metrics.get("match_win_rate") is not None:
        notes.append(f"local match WR {round(metrics['match_win_rate'] * 100)}%")
    if metrics.get("kd_ratio") is not None:
        notes.append(f"local K/D {metrics['kd_ratio']}")
    if grid_summary.get("series_win_rate") is not None:
        notes.append(f"GRID series WR {round(grid_summary['series_win_rate'])}%")
    if grid_summary.get("first_kill_rate") is not None:
        notes.append(f"first kill {round(grid_summary['first_kill_rate'])}%")
    adjusted = 50.0 + ((score - 50.0) * max(0.35, confidence))
    return round(max(0.0, min(100.0, adjusted)), 2), notes[:4]


def pre_match_edge(team1: dict[str, Any], team2: dict[str, Any]) -> dict[str, Any]:
    summary1 = grid_stats_summary(team1.get("grid_stats"))
    summary2 = grid_stats_summary(team2.get("grid_stats"))
    score1, notes1 = team_score(team1.get("metrics") or {}, summary1)
    score2, notes2 = team_score(team2.get("metrics") or {}, summary2)
    total = score1 + score2
    probability1 = round(score1 / total, 4) if total else 0.5
    probability2 = round(1 - probability1, 4)
    edge = round(score1 - score2, 2)
    leader = team1.get("name") if edge >= 0 else team2.get("name")
    confidence_volume = (
        (team1.get("metrics") or {}).get("matches_played") or 0
    ) + ((team2.get("metrics") or {}).get("matches_played") or 0) + (summary1.get("series_count") or 0) + (summary2.get("series_count") or 0)
    return {
        "team1_score": score1,
        "team2_score": score2,
        "team1_probability": probability1,
        "team2_probability": probability2,
        "edge": edge,
        "leader": leader,
        "confidence": "high" if confidence_volume >= 60 else "medium" if confidence_volume >= 25 else "low",
        "team1_notes": notes1,
        "team2_notes": notes2,
        "team1_grid_summary": summary1,
        "team2_grid_summary": summary2,
    }


def estimate_backfill(days: int, window_days: int, max_pages: int, max_matches: int, request_limit_per_minute: int, stats_limit: int, refresh_stats: bool) -> dict[str, Any]:
    windows = max(1, (days + window_days - 1) // window_days)
    central_requests = windows * max_pages
    max_series_state_requests = max_matches
    stats_requests = min(100, max_matches) if refresh_stats else 0
    total_requests = central_requests + max_series_state_requests + stats_requests
    main_minutes = (central_requests + max_series_state_requests) / max(1, request_limit_per_minute)
    stats_minutes = stats_requests / max(1, stats_limit)
    eta_minutes = round(main_minutes + stats_minutes, 1)
    return {
        "days": days,
        "window_days": window_days,
        "windows": windows,
        "max_pages": max_pages,
        "max_matches": max_matches,
        "estimated_requests": total_requests,
        "estimated_main_requests": central_requests + max_series_state_requests,
        "estimated_stats_requests": stats_requests,
        "eta_minutes": eta_minutes,
        "eta_text": f"~{eta_minutes} min",
        "generated_at": datetime.utcnow().isoformat(),
    }

from fastapi.testclient import TestClient

from app.web.main import GridSyncRequest, _recovery_payload, app


def test_health_and_readiness_endpoints():
    client = TestClient(app)
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "database": "available"}


def test_dashboard_page_loads():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "CS2 Tier-1 Analytics" in response.text


def test_summary_endpoint_loads():
    client = TestClient(app)
    response = client.get("/api/summary")
    assert response.status_code == 200
    data = response.json()
    assert {"teams", "players", "matches", "maps", "player_stats"}.issubset(data)


def test_teams_include_match_count_and_last_match_date():
    response = TestClient(app).get("/api/teams?limit=1")
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert {"matches", "last_played", "window_matches", "grid_series_win_rate"}.issubset(rows[0])


def test_team_detail_distinguishes_results_from_detailed_maps():
    team_id = TestClient(app).get("/api/teams?limit=1").json()[0]["id"]
    data = TestClient(app).get(f"/api/teams/{team_id}?window=50").json()["team"]
    assert {"matches", "matches_with_maps", "results_only_matches", "maps_played"}.issubset(data)
    assert {"rank", "points", "ranking_date"}.issubset(data)
    assert data["matches_with_maps"] <= data["matches"]
    assert set(data["form_windows"]) == {"5", "10", "20", "50"}
    assert [row["top"] for row in data["ranked_opponents"]] == [10, 30, 50]
    assert {"player_form", "upcoming_matches"}.issubset(data)
    assert {"event_priority", "maps"}.issubset(data["recent_matches"][0])


def test_matches_expose_data_completeness():
    rows = TestClient(app).get("/api/matches?status=all&limit=1").json()["items"]
    assert rows
    assert {"level", "flags", "player_stats", "rounds"}.issubset(rows[0]["completeness"])


def test_detailed_match_exposes_team_and_per_map_statistics():
    rows = TestClient(app).get("/api/matches?status=completed&limit=100").json()["items"]
    detailed = next(row for row in rows if row["completeness"]["flags"]["players"])
    match = TestClient(app).get(f"/api/matches/{detailed['id']}").json()["match"]
    assert {"id", "name"}.issubset(match["team1"])
    assert match["event_priority"]["tier"] == "other"
    assert {"kills", "deaths", "assists", "kd_ratio", "avg_adr"}.issubset(match["team1_stats"])
    assert match["maps"]
    assert {"team1_stats", "team2_stats", "first_half_team1", "second_half_team1"}.issubset(match["maps"][0])
    assert {"team1_rounds", "team2_rounds", "round_history", "picked_by_team_id", "player_stats"}.issubset(match["maps"][0])
    assert match["maps"][0]["player_stats"]
    assert {"team1_rounds", "team2_rounds", "best_of"}.issubset(match)
    assert {"player_id", "team_id", "map_id"}.issubset(match["player_stats"][0])


def test_comparison_exposes_head_to_head_and_common_opponents():
    match = TestClient(app).get("/api/upcoming?limit=1&days=90").json()[0]
    response = TestClient(app).get(f"/api/compare?team1_id={match['team1']['id']}&team2_id={match['team2']['id']}&window=20")
    assert response.status_code == 200
    data = response.json()
    assert {"head_to_head", "common_opponents", "coverage", "advantages"}.issubset(data)
    assert {"form", "recent_matches"}.issubset(data["team1"])


def test_upcoming_tournaments_are_prioritized():
    response = TestClient(app).get("/api/upcoming/tournaments?days=90")
    assert response.status_code == 200
    data = response.json()
    assert data
    assert {"name", "matches", "tier", "priority", "label"}.issubset(data[0])


def test_validate_endpoint_loads():
    client = TestClient(app)
    response = client.get("/api/validate")
    assert response.status_code == 200
    assert "issues" in response.json()


def test_operations_router_preserves_public_endpoints():
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/api/backup",
        "/api/export",
        "/api/metrics/compute",
        "/api/validate",
    }.issubset(paths)
    computed = client.post("/api/metrics/compute")
    assert computed.status_code == 200
    assert computed.json()["ok"] is True


def test_data_status_endpoint_loads():
    client = TestClient(app)
    response = client.get("/api/data-status")
    assert response.status_code == 200
    data = response.json()
    assert {"cursor", "jobs", "latest_match_time"}.issubset(data)


def test_automation_settings_are_persistent_and_validated():
    client = TestClient(app)
    response = client.put("/api/automation", json={"enabled": False, "interval_minutes": 45})
    assert response.status_code == 200
    assert response.json()["interval_minutes"] == 45
    assert client.get("/api/automation").json()["enabled"] is False
    assert {
        "scheduler_running",
        "worker_running",
        "watchdog_running",
        "queue_size",
        "last_automation_job",
    }.issubset(client.get("/api/automation").json())
    assert client.put("/api/automation", json={"enabled": True, "interval_minutes": 10}).status_code == 422


def test_backup_and_export_endpoints_create_files():
    client = TestClient(app)
    assert client.post("/api/backup").json()["ok"] is True
    assert client.post("/api/export").json()["ok"] is True


def test_backfill_calendar_endpoint_loads():
    client = TestClient(app)
    response = client.get("/api/backfill/calendar?days=3")
    assert response.status_code == 200
    data = response.json()
    assert {"from", "to", "summary", "days"}.issubset(data)
    assert len(data["days"]) >= 3
    assert "stale" in data["summary"]


def test_backfill_request_accepts_large_historical_match_limit():
    payload = GridSyncRequest(mode="backfill", max_matches=1500)
    assert payload.max_matches == 1500


def test_audit_sync_mode_is_accepted():
    payload = GridSyncRequest(mode="audit", max_pages=50)
    assert payload.mode == "audit"


def test_repair_sync_mode_is_accepted():
    payload = GridSyncRequest(mode="repair", days=30, max_matches=100, post_pipeline=False)
    assert payload.mode == "repair"


def test_team_sync_mode_accepts_selected_team_and_period():
    payload = GridSyncRequest(mode="team", team_id=42, days=90, force_refresh=False, post_pipeline=False)
    assert payload.team_id == 42
    assert payload.days == 90


def test_match_sync_mode_accepts_selected_match():
    payload = GridSyncRequest(mode="match", match_id=7, post_pipeline=False)
    assert payload.match_id == 7
    assert payload.mode == "match"


def test_pandascore_upcoming_sync_mode_is_accepted():
    payload = GridSyncRequest(mode="pandascore-upcoming", days=14, top_limit=50)
    assert payload.mode == "pandascore-upcoming"


def test_pandascore_results_sync_mode_is_accepted():
    payload = GridSyncRequest(mode="pandascore-results", days=7, top_limit=50)
    assert payload.mode == "pandascore-results"


def test_update_all_sync_mode_is_accepted():
    payload = GridSyncRequest(mode="update-all", days=7, history_days=14, participant_history_days=180, top_limit=50)
    assert payload.mode == "update-all"
    assert payload.participant_history_days == 180


def test_only_automation_jobs_are_recoverable_after_restart():
    manual = GridSyncRequest(mode="update-all", trigger="manual").model_dump_json()
    automated = GridSyncRequest(mode="update-all", trigger="automation").model_dump_json()
    assert _recovery_payload("grid-update-all", manual) is None
    recovered = _recovery_payload("grid-update-all", automated)
    assert recovered is not None
    assert recovered.trigger == "recovery"


def test_valve_ranking_sync_mode_is_accepted():
    payload = GridSyncRequest(mode="valve-ranking", top_limit=100, post_pipeline=False)
    assert payload.mode == "valve-ranking"


def test_cancel_unknown_job_returns_readable_error():
    response = TestClient(app).post("/api/sync/grid/jobs/not-running/cancel")
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "Active job not found"}


def test_team_players_missing_team_returns_error():
    client = TestClient(app)
    response = client.get("/api/teams/-999/players")
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_match_preview_missing_match_returns_error():
    client = TestClient(app)
    response = client.get("/api/matches/-999/preview")
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_matches_endpoint_is_paginated_and_defaults_to_completed():
    response = TestClient(app).get("/api/matches?page_size=3")
    assert response.status_code == 200
    data = response.json()
    assert {"items", "total", "page", "page_size", "pages"}.issubset(data)
    assert len(data["items"]) <= 3
    assert all(item["status"] == "completed" for item in data["items"])


def test_matches_endpoint_rejects_unknown_status_without_crashing():
    response = TestClient(app).get("/api/matches?status=unknown")
    assert response.status_code == 200
    assert response.json()["error"] == "Unknown match status"


def test_matches_endpoint_filters_by_detail_level():
    client = TestClient(app)
    with_maps = client.get("/api/matches?status=completed&detail_level=maps&page_size=10")
    assert with_maps.status_code == 200
    assert all(item["completeness"]["flags"]["maps"] for item in with_maps.json()["items"])

    result_only = client.get("/api/matches?status=completed&detail_level=result_only&page_size=10")
    assert result_only.status_code == 200
    assert all(not item["completeness"]["flags"]["maps"] for item in result_only.json()["items"])


def test_data_coverage_endpoint_reports_detail_counts():
    response = TestClient(app).get("/api/data-coverage?days=3650")
    assert response.status_code == 200
    data = response.json()
    assert {
        "matches",
        "result_only",
        "with_maps",
        "with_players",
        "with_rounds",
        "map_coverage",
        "player_coverage",
        "round_coverage",
    }.issubset(data)
    assert data["matches"] >= data["with_maps"] >= data["with_players"]


def test_data_coverage_endpoint_accepts_explicit_dates():
    response = TestClient(app).get(
        "/api/data-coverage?date_from=2026-01-01T00:00:00Z&date_to=2026-12-31T23:59:59Z"
    )
    assert response.status_code == 200
    assert "matches" in response.json()


def test_period_quality_endpoint_reports_detail_levels():
    response = TestClient(app).get("/api/data-quality/period?days=30")
    assert response.status_code == 200
    data = response.json()
    assert {
        "matches",
        "levels",
        "map_coverage",
        "player_coverage",
        "round_coverage",
        "repair_candidates",
        "days",
    }.issubset(data)


def test_collection_endpoints_clamp_large_limits():
    client = TestClient(app)
    assert len(client.get("/api/upcoming?limit=100000").json()) <= 500
    assert len(client.get("/api/players?limit=100000").json()) <= 500
    assert len(client.get("/api/player-stats?limit=100000").json()) <= 500
    assert len(client.get("/api/jobs?limit=100000").json()) <= 200

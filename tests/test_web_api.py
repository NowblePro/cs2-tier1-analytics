from fastapi.testclient import TestClient

from app.web.main import app


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


def test_validate_endpoint_loads():
    client = TestClient(app)
    response = client.get("/api/validate")
    assert response.status_code == 200
    assert "issues" in response.json()


def test_data_status_endpoint_loads():
    client = TestClient(app)
    response = client.get("/api/data-status")
    assert response.status_code == 200
    data = response.json()
    assert {"cursor", "jobs", "latest_match_time"}.issubset(data)


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

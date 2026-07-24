from datetime import UTC, datetime, timedelta

import httpx

from app.config import Settings
from app.grid.client import GridApiError, GridClient
from app.http_retry import graphql_rate_limit_delay, is_retryable_status, retry_delay
from app.pandascore.client import PandaScoreClient
from app.valve_vrs.client import ValveVrsClient


def test_retry_helpers_respect_retry_after_and_rate_limit_reset():
    assert is_retryable_status(429)
    assert is_retryable_status(503)
    assert not is_retryable_status(403)
    assert retry_delay(0, {"Retry-After": "7"}) == 7
    future = datetime.now(UTC) + timedelta(seconds=10)
    delay = retry_delay(0, {"Retry-After": future.strftime("%a, %d %b %Y %H:%M:%S GMT")})
    assert 0 <= delay <= 10
    assert graphql_rate_limit_delay(
        [{"message": "rate limit", "extensions": {"rateLimitResetsIn": "PT19S"}}],
        0,
    ) == 19


def test_grid_retries_graphql_rate_limit_using_reported_delay():
    requests = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "You have exceeded your rate limit",
                            "extensions": {
                                "errorType": "UNAVAILABLE",
                                "rateLimitResetsIn": "PT2S",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": {"ok": True}})

    client = GridClient(
        Settings(
            grid_api_key="test",
            grid_request_limit_per_minute=60_000,
            max_retries=1,
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    try:
        assert client._post(client.central_url, "query { ok }", {}) == {"ok": True}
    finally:
        client.close()
    assert requests == 2
    assert 2 in sleeps


def test_grid_does_not_retry_forbidden_response():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(403, text="forbidden")

    client = GridClient(
        Settings(grid_api_key="test", max_retries=3),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    try:
        try:
            client._post(client.central_url, "query { ok }", {})
        except GridApiError as exc:
            assert "HTTP 403" in str(exc)
        else:
            raise AssertionError("Expected GridApiError")
    finally:
        client.close()
    assert requests == 1


def test_pandascore_retries_network_error():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json=[])

    client = PandaScoreClient(
        Settings(pandascore_api_key="test", max_retries=1),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    try:
        assert client.search_teams("Falcons") == []
    finally:
        client.close()
    assert requests == 2


def test_valve_retries_server_error():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(503, text="temporary")
        return httpx.Response(200, text="ranking")

    client = ValveVrsClient(
        Settings(max_retries=1),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    try:
        assert client._get("https://example.test/ranking").text == "ranking"
    finally:
        client.close()
    assert requests == 2

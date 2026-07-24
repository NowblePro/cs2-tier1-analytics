from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.http_retry import is_retryable_status, retry_delay


class PandaScoreApiError(RuntimeError):
    pass


class PandaScoreClient:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.settings = settings or get_settings()
        if not self.settings.pandascore_api_key:
            raise PandaScoreApiError("PANDASCORE_API_KEY is not set")
        self.base_url = self.settings.pandascore_base_url.rstrip("/")
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {self.settings.pandascore_api_key}"},
            timeout=self.settings.request_timeout,
            transport=transport,
        )
        self._sleep = sleep

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        response: httpx.Response | None = None
        last_error: httpx.RequestError | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.client.get(f"{self.base_url}{path}", params=params)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    self._sleep(retry_delay(attempt))
                    continue
                raise PandaScoreApiError(f"PandaScore request failed: {exc}") from exc
            if not is_retryable_status(response.status_code):
                break
            if attempt < self.settings.max_retries:
                self._sleep(retry_delay(attempt, response.headers))
        if response is None:
            raise PandaScoreApiError(f"PandaScore request failed: {last_error}") from last_error
        if response.status_code >= 400:
            raise PandaScoreApiError(f"PandaScore returned HTTP {response.status_code}: {response.text[:500]}")
        return response

    def list_upcoming(
        self,
        date_from: datetime,
        date_to: datetime,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        response = self._get(
            "/csgo/matches/upcoming",
            {
                "page": page,
                "per_page": min(max(per_page, 1), 100),
                "range[scheduled_at]": f"{self._iso(date_from)},{self._iso(date_to)}",
                "sort": "scheduled_at",
            },
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise PandaScoreApiError("Unexpected PandaScore upcoming response")
        total = int(response.headers.get("X-Total", len(payload)))
        return payload, total

    def list_past(
        self,
        date_from: datetime,
        date_to: datetime,
        *,
        page: int = 1,
        per_page: int = 100,
        team_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "page": page,
            "per_page": min(max(per_page, 1), 100),
            "range[scheduled_at]": f"{self._iso(date_from)},{self._iso(date_to)}",
            "sort": "-scheduled_at",
        }
        if team_id:
            params["filter[opponent_id]"] = team_id
        response = self._get(
            "/csgo/matches/past",
            params,
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise PandaScoreApiError("Unexpected PandaScore past matches response")
        return payload, int(response.headers.get("X-Total", len(payload)))

    def search_teams(self, name: str) -> list[dict[str, Any]]:
        response = self._get("/csgo/teams", {"search[name]": name, "per_page": 100})
        payload = response.json()
        if not isinstance(payload, list):
            raise PandaScoreApiError("Unexpected PandaScore teams response")
        return payload

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "PandaScoreClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

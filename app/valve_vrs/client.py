from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.http_retry import is_retryable_status, retry_delay


class ValveVrsApiError(RuntimeError):
    pass


class ValveVrsClient:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.settings = settings or get_settings()
        self.base_url = self.settings.valve_vrs_github_api_url.rstrip("/")
        self.client = httpx.Client(
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "cs2-tier1-analytics",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=self.settings.request_timeout,
            transport=transport,
        )
        self._sleep = sleep

    def _get(self, url: str) -> httpx.Response:
        response: httpx.Response | None = None
        last_error: httpx.RequestError | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.client.get(url)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    self._sleep(retry_delay(attempt))
                    continue
                raise ValveVrsApiError(f"Valve VRS GitHub request failed: {exc}") from exc
            if not is_retryable_status(response.status_code):
                break
            if attempt < self.settings.max_retries:
                self._sleep(retry_delay(attempt, response.headers))
        if response is None:
            raise ValveVrsApiError(f"Valve VRS GitHub request failed: {last_error}") from last_error
        if response.status_code >= 400:
            raise ValveVrsApiError(f"Valve VRS GitHub returned HTTP {response.status_code}: {response.text[:500]}")
        return response

    def fetch_latest_global(self, year: int | None = None) -> tuple[str, str]:
        current_year = year or datetime.now(UTC).year
        files: list[dict[str, Any]] = []
        for candidate_year in (current_year, current_year - 1):
            response = self._get(f"{self.base_url}/contents/invitation/{candidate_year}")
            payload = response.json()
            if isinstance(payload, list):
                files = [item for item in payload if str(item.get("name", "")).startswith("standings_global_") and str(item.get("name", "")).endswith(".md")]
            if files:
                break
        if not files:
            raise ValveVrsApiError("No global Valve VRS standings file found")
        latest = max(files, key=lambda item: item["name"])
        source_url = latest.get("html_url") or latest.get("download_url")
        download_url = latest.get("download_url")
        if not source_url or not download_url:
            raise ValveVrsApiError("Latest Valve VRS file has no download URL")
        return self._get(download_url).text, source_url

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ValveVrsClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

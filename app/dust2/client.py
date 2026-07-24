from __future__ import annotations

import httpx

from app.config import Settings, get_settings


class Dust2FetchError(RuntimeError):
    pass


class Dust2Client:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self.client = httpx.Client(
            timeout=self.settings.request_timeout,
            follow_redirects=True,
            headers={"User-Agent": "cs2-tier1-analytics/0.1"},
            transport=transport,
        )

    def fetch_match(self, url: str) -> str:
        response = self.client.get(url)
        if response.status_code == 403:
            raise Dust2FetchError(f"Dust2 returned HTTP 403 for {url}")
        if response.status_code >= 400:
            raise Dust2FetchError(f"Dust2 returned HTTP {response.status_code} for {url}")
        return response.text

    def fetch_results(self) -> str:
        response = self.client.get("https://www.dust2.us/results")
        if response.status_code == 403:
            raise Dust2FetchError("Dust2 returned HTTP 403 for results")
        if response.status_code >= 400:
            raise Dust2FetchError(f"Dust2 returned HTTP {response.status_code} for results")
        return response.text

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Dust2Client":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

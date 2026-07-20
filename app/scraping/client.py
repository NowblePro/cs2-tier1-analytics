from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings


class HltvBlockedError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    status_code: int | None
    html: str
    from_cache: bool
    file_path: Path


class HltvClient:
    def __init__(self, settings: Settings | None = None, from_cache: bool = False, force_refresh: bool = False, dry_run: bool = False):
        self.settings = settings or get_settings()
        self.from_cache = from_cache
        self.force_refresh = force_refresh
        self.dry_run = dry_run
        self.pages_requested = 0
        self.skipped_pages = 0
        self.blocked_count = 0
        self._client = httpx.Client(
            timeout=self.settings.request_timeout,
            headers={"User-Agent": "cs2-tier1-analytics/0.1 contact: local research scraper"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        safe = quote(url, safe="")[:80]
        return self.settings.raw_html_dir / f"{digest}-{safe}.html"

    def fetch(self, path_or_url: str) -> FetchResult:
        url = path_or_url if path_or_url.startswith("http") else f"{self.settings.hltv_base_url.rstrip('/')}/{path_or_url.lstrip('/')}"
        cache_path = self._cache_path(url)
        if cache_path.exists() and (self.from_cache or not self.force_refresh):
            return FetchResult(url=url, status_code=None, html=cache_path.read_text(encoding="utf-8"), from_cache=True, file_path=cache_path)
        if self.from_cache:
            raise FileNotFoundError(f"No cached HTML for {url}")
        if self.dry_run:
            self.skipped_pages += 1
            return FetchResult(url=url, status_code=None, html="", from_cache=False, file_path=cache_path)

        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            self.pages_requested += 1
            response = self._client.get(url)
            if response.status_code in {403, 429}:
                self.blocked_count += 1
                raise HltvBlockedError(f"HLTV returned HTTP {response.status_code} for {url}")
            if response.status_code < 500:
                response.raise_for_status()
                cache_path.write_text(response.text, encoding="utf-8")
                return FetchResult(url=url, status_code=response.status_code, html=response.text, from_cache=False, file_path=cache_path)
            last_exc = httpx.HTTPStatusError("temporary server error", request=response.request, response=response)
            if attempt < self.settings.max_retries:
                time.sleep((2**attempt) + random.uniform(self.settings.request_delay_min, self.settings.request_delay_max))
        assert last_exc is not None
        raise last_exc


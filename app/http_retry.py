from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Mapping


RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_HTTP_STATUSES


def retry_delay(
    attempt: int,
    headers: Mapping[str, str] | None = None,
    *,
    default_cap: float = 30,
) -> float:
    retry_after = (headers or {}).get("Retry-After")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 300.0))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0.0, min((retry_at - datetime.now(UTC)).total_seconds(), 300.0))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(default_cap, float(2**attempt))


def graphql_rate_limit_delay(errors: list[dict], attempt: int) -> float | None:
    for error in errors:
        message = str(error.get("message", "")).lower()
        extensions = error.get("extensions") or {}
        if "rate limit" not in message and extensions.get("errorType") != "UNAVAILABLE":
            continue
        reset = str(extensions.get("rateLimitResetsIn", ""))
        if reset.startswith("PT") and reset.endswith("S"):
            try:
                return max(0.0, min(float(reset[2:-1]), 300.0))
            except ValueError:
                pass
        return retry_delay(attempt)
    return None

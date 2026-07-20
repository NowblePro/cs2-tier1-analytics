import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup, Tag


INT_RE = re.compile(r"-?\d+")
FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def int_or_none(value: str | None) -> int | None:
    match = INT_RE.search(value or "")
    return int(match.group(0)) if match else None


def float_or_none(value: str | None) -> float | None:
    match = FLOAT_RE.search((value or "").replace("%", ""))
    return float(match.group(0)) if match else None


def attr_int(tag: Tag, name: str) -> int | None:
    return int_or_none(tag.get(name))


def parse_hltv_id_from_href(href: str | None, prefix: str) -> int | None:
    if not href:
        return None
    match = re.search(rf"/{re.escape(prefix)}/(\d+)", href)
    return int(match.group(1)) if match else None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        ts = int(value)
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


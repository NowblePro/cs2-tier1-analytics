from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.dust2.client import Dust2Client
from app.dust2.parser import Dust2Match, parse_dust2_match
from app.models.schema import Match, Team
from app.repositories.team_aliases import canonical_team_key

DUST2_BASE_URL = "https://www.dust2.us"


@dataclass(frozen=True)
class Dust2Candidate:
    url: str
    score: int
    reason: str
    parsed: Dust2Match | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "score": self.score,
            "reason": self.reason,
            "parsed": self.parsed.summary() if self.parsed else None,
        }


def resolve_dust2_match(session: Session, client: Dust2Client, match_id: int, *, max_candidates: int = 8) -> list[Dust2Candidate]:
    match = session.get(Match, match_id)
    if match is None:
        raise ValueError(f"Local match {match_id} was not found")
    team1 = session.get(Team, match.team1_id) if match.team1_id else None
    team2 = session.get(Team, match.team2_id) if match.team2_id else None
    if team1 is None or team2 is None:
        raise ValueError(f"Local match {match_id} does not have two teams")

    results_html = client.fetch_results()
    urls = _candidate_urls(results_html, team1.name, team2.name)
    candidates: list[Dust2Candidate] = []
    for url in urls[:max_candidates]:
        html = client.fetch_match(url)
        parsed = parse_dust2_match(html)
        score, reason = _score_candidate(parsed, team1.name, team2.name)
        candidates.append(Dust2Candidate(url=url, score=score, reason=reason, parsed=parsed))
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def best_dust2_match(session: Session, client: Dust2Client, match_id: int) -> Dust2Candidate | None:
    candidates = resolve_dust2_match(session, client, match_id)
    if not candidates:
        return None
    best = candidates[0]
    return best if best.score >= 70 else None


def _candidate_urls(html: str, team1: str, team2: str) -> list[str]:
    doc = BeautifulSoup(html, "html.parser")
    team_keys = {_slug_key(team1), _slug_key(team2)}
    urls: list[str] = []
    seen: set[str] = set()
    for link in doc.find_all("a", href=True):
        href = str(link["href"])
        if not href.startswith("/matches/"):
            continue
        parts = [part for part in href.split("/") if part]
        if len(parts) < 3:
            continue
        slug = parts[2]
        if all(key and key in _slug_key(slug) for key in team_keys):
            url = urljoin(DUST2_BASE_URL, href.split("#", 1)[0].removesuffix("/statistic"))
            if url not in seen:
                urls.append(url)
                seen.add(url)
    return urls


def _score_candidate(parsed: Dust2Match, team1: str, team2: str) -> tuple[int, str]:
    parsed_keys = {_slug_key(parsed.team1_name or ""), _slug_key(parsed.team2_name or "")}
    target_keys = {_slug_key(team1), _slug_key(team2)}
    score = 0
    reasons: list[str] = []
    if target_keys <= parsed_keys:
        score += 60
        reasons.append("teams")
    if parsed.maps:
        score += 25
        reasons.append("maps")
    if parsed.rounds:
        score += 25
        reasons.append("rounds")
    return score, "+".join(reasons) or "weak"


def _slug_key(value: str) -> str:
    key = canonical_team_key(value)
    aliases = {
        "ninjasinpyjamas": "nip",
        "nip": "nip",
        "auroragaming": "aurora",
        "aurora": "aurora",
    }
    return aliases.get(key, key)

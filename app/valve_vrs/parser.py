from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ValveRankingTeam:
    rank: int
    points: int
    name: str


@dataclass(frozen=True)
class ValveRanking:
    ranking_date: datetime
    teams: list[ValveRankingTeam]


def parse_valve_ranking(markdown: str, limit: int = 100) -> ValveRanking:
    date_match = re.search(r"Standings as of\s+(\d{4})_(\d{2})_(\d{2})", markdown, re.IGNORECASE)
    if not date_match:
        raise ValueError("Valve VRS ranking date was not found")
    ranking_date = datetime(*(int(value) for value in date_match.groups()))
    teams: list[ValveRankingTeam] = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if len(columns) < 3 or not columns[0].isdigit() or not columns[1].replace(",", "").isdigit():
            continue
        teams.append(ValveRankingTeam(rank=int(columns[0]), points=int(columns[1].replace(",", "")), name=columns[2]))
        if len(teams) >= limit:
            break
    if not teams:
        raise ValueError("Valve VRS ranking table is empty")
    return ValveRanking(ranking_date=ranking_date, teams=teams)

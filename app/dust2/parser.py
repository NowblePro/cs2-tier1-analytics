from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from app.grid.ingest import normalize_map_name

KNOWN_DUST2_MAPS = {"Ancient", "Anubis", "Cache", "Dust2", "Dust 2", "Inferno", "Mirage", "Nuke", "Overpass", "Train", "Vertigo"}
END_METHOD_BY_ICON = {
    "bomb_defused": ("CT", "bomb_defused"),
    "bomb_exploded": ("T", "bomb_exploded"),
    "ct_win": ("CT", "ct_win"),
    "stopwatch": ("CT", "time_expired"),
    "t_win": ("T", "t_win"),
}


@dataclass(frozen=True)
class Dust2PlayerStat:
    map_number: int
    team_name: str
    nickname: str
    real_name: str | None = None
    kills: int | None = None
    deaths: int | None = None
    kd_diff: int | None = None
    adr: float | None = None
    kast: float | None = None
    rating: float | None = None


@dataclass(frozen=True)
class Dust2Round:
    map_number: int
    round_number: int
    winner_team_name: str
    winner_side: str | None
    end_method: str | None
    score_team1_after: int | None
    score_team2_after: int | None
    is_pistol: bool


@dataclass(frozen=True)
class Dust2Map:
    map_number: int
    name: str
    score_team1: int
    score_team2: int
    winner_team_name: str | None = None


@dataclass(frozen=True)
class Dust2Match:
    title: str | None
    team1_name: str | None
    team2_name: str | None
    maps: list[Dust2Map] = field(default_factory=list)
    rounds: list[Dust2Round] = field(default_factory=list)
    player_stats: list[Dust2PlayerStat] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "title": self.title,
            "teams": [self.team1_name, self.team2_name],
            "maps": [
                {
                    "map_number": item.map_number,
                    "name": item.name,
                    "score": f"{item.score_team1}-{item.score_team2}",
                    "winner": item.winner_team_name,
                }
                for item in self.maps
            ],
            "rounds": len(self.rounds),
            "pistol_rounds": len([item for item in self.rounds if item.is_pistol]),
            "player_stats": len(self.player_stats),
        }


def parse_dust2_match(html: str) -> Dust2Match:
    doc = BeautifulSoup(html, "html.parser")
    title = doc.title.get_text(strip=True) if doc.title else None
    teams = _parse_header_teams(doc)
    maps = _parse_maps(doc, teams)
    rounds = _parse_rounds(doc, teams)
    stats = _parse_player_stats(doc)
    return Dust2Match(title=title, team1_name=teams[0] if teams else None, team2_name=teams[1] if len(teams) > 1 else None, maps=maps, rounds=rounds, player_stats=stats)


def _parse_header_teams(doc: BeautifulSoup) -> list[str]:
    teams: list[str] = []
    score_heading = doc.select_one(".matchpage-header-title")
    if score_heading:
        for img in score_heading.select("img[alt]"):
            name = _clean(img.get("alt"))
            if name and name not in teams:
                teams.append(name)
    if len(teams) >= 2:
        return teams[:2]
    for wrapper in doc.select(".lineup, .round-breakdown-team-wrapper"):
        logo = wrapper.select_one("img.team-logo[alt], img[alt]")
        name = _clean(logo.get("alt")) if logo else None
        if name and name not in teams:
            teams.append(name)
        if len(teams) >= 2:
            break
    return teams[:2]


def _parse_maps(doc: BeautifulSoup, teams: list[str]) -> list[Dust2Map]:
    maps: list[Dust2Map] = []
    for node in doc.select(".map-container"):
        text = [item for item in (_clean(value) for value in node.get_text("\n", strip=True).split("\n")) if item]
        map_name = next((item for item in text if item in KNOWN_DUST2_MAPS), None)
        scores = [int(item) for item in text if re.fullmatch(r"\d+", item)]
        if not map_name or len(scores) < 2:
            continue
        score1, score2 = scores[-2], scores[-1]
        winner = None
        if teams:
            if score1 > score2:
                winner = teams[0]
            elif len(teams) > 1 and score2 > score1:
                winner = teams[1]
        maps.append(
            Dust2Map(
                map_number=len(maps) + 1,
                name=normalize_map_name(map_name),
                score_team1=score1,
                score_team2=score2,
                winner_team_name=winner,
            )
        )
    return maps


def _parse_rounds(doc: BeautifulSoup, teams: list[str]) -> list[Dust2Round]:
    rounds: list[Dust2Round] = []
    for map_number, container in enumerate(doc.select(".round-breakdown-container"), start=1):
        per_round: dict[int, Dust2Round] = {}
        for wrapper in container.select(".round-breakdown-team-wrapper"):
            logo = wrapper.select_one(".round-breakdown-team-logo-container img[alt], img.team-logo[alt]")
            winner_name = _clean(logo.get("alt")) if logo else None
            if not winner_name:
                continue
            for half_index, half in enumerate(wrapper.select(".round-breakdown-half")):
                for cell_index, cell in enumerate(half.select(".round-breakdown-cell")):
                    icon = cell.select_one("img.round-breakdown-icon")
                    if not icon:
                        continue
                    round_number = half_index * 12 + cell_index + 1
                    method_key = _icon_key(icon.get("src"))
                    side, method = END_METHOD_BY_ICON.get(method_key, (None, method_key or None))
                    score1, score2 = _parse_score(icon.get("title") or icon.get("alt"))
                    if teams and winner_name == teams[1]:
                        score1, score2 = score1, score2
                    per_round[round_number] = Dust2Round(
                        map_number=map_number,
                        round_number=round_number,
                        winner_team_name=winner_name,
                        winner_side=side,
                        end_method=method,
                        score_team1_after=score1,
                        score_team2_after=score2,
                        is_pistol=round_number in {1, 13} and round_number <= 24,
                    )
        rounds.extend(per_round[key] for key in sorted(per_round))
    return rounds


def _parse_player_stats(doc: BeautifulSoup) -> list[Dust2PlayerStat]:
    rows: list[Dust2PlayerStat] = []
    tables = doc.select(".match-result-table")
    for index, table in enumerate(tables):
        map_number = index // 2
        team_node = table.select_one(".match-stat-team-name")
        current_team = _clean(team_node.get_text(" ", strip=True)) if team_node else None
        if current_team is None:
            continue
        for player_row in table.select("tbody tr"):
            player_cell = player_row.select_one("td.match-stat-player-team")
            nickname_node = player_cell.select_one("b") if player_cell else None
            nickname = _clean(nickname_node.get_text(" ", strip=True)) if nickname_node else None
            if not nickname:
                continue
            cells = player_row.select("td")
            if len(cells) < 6:
                continue
            kills, deaths = _parse_kd(cells[1].get_text(" ", strip=True))
            rows.append(
                Dust2PlayerStat(
                    map_number=map_number,
                    team_name=current_team,
                    nickname=nickname,
                    real_name=_player_real_name(player_cell.get_text(" ", strip=True) if player_cell else "", nickname),
                    kills=kills,
                    deaths=deaths,
                    kd_diff=_parse_int(cells[2].get_text(" ", strip=True)),
                    adr=_parse_float(cells[3].get_text(" ", strip=True)),
                    kast=_parse_percent(cells[4].get_text(" ", strip=True)),
                    rating=_parse_float(cells[5].get_text(" ", strip=True)),
                )
            )
    return rows


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def _icon_key(src: str | None) -> str | None:
    if not src:
        return None
    return src.rsplit("/", 1)[-1].replace(".svg", "")


def _parse_score(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    match = re.search(r"(\d+)\s*-\s*(\d+)", value)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _parse_kd(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", value)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _parse_int(value: str) -> int | None:
    match = re.search(r"[+-]?\d+", value)
    return int(match.group(0)) if match else None


def _parse_float(value: str) -> float | None:
    match = re.search(r"[+-]?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def _parse_percent(value: str) -> float | None:
    parsed = _parse_float(value)
    return parsed if parsed is not None else None


def _player_real_name(value: str, nickname: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.replace(nickname, f"'{nickname}'", 1) if f"'{nickname}'" not in text else text
    return text or None

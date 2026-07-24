from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.grid.ids import stable_negative_id
from app.grid.ingest import normalize_map_name
from app.models.schema import ExternalEntityMap, Match, MatchMap, Player, PlayerMapStat, Round
from app.scraping.player_stats_parser import calculate_kd_ratio
from app.scraping.round_parser import is_pistol_round


class AwpyUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedDemo:
    path: str
    header: dict[str, Any]
    rounds: list[dict[str, Any]]
    kills: list[dict[str, Any]]
    damages: list[dict[str, Any]]

    @property
    def map_name(self) -> str:
        return normalize_map_name(str(self.header.get("map_name") or self.header.get("map") or ""))

    def inspect(self, sample_rows: int = 3) -> dict[str, Any]:
        return {
            "path": self.path,
            "header": self.header,
            "map_name": self.map_name,
            "counts": {
                "rounds": len(self.rounds),
                "kills": len(self.kills),
                "damages": len(self.damages),
            },
            "columns": {
                "rounds": sorted(_columns(self.rounds)),
                "kills": sorted(_columns(self.kills)),
                "damages": sorted(_columns(self.damages)),
            },
            "samples": {
                "rounds": self.rounds[:sample_rows],
                "kills": self.kills[:sample_rows],
                "damages": self.damages[:sample_rows],
            },
            "player_totals": _player_totals(self.kills, self.damages),
            "round_summary": _round_summary(self.rounds),
        }


@dataclass(frozen=True)
class DemoImportResult:
    match_id: int
    match_map_id: int
    map_name: str
    rounds_imported: int
    player_stats_imported: int
    players_unmapped_to_team: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "match_map_id": self.match_map_id,
            "map_name": self.map_name,
            "rounds_imported": self.rounds_imported,
            "player_stats_imported": self.player_stats_imported,
            "players_unmapped_to_team": self.players_unmapped_to_team,
        }


def inspect_demo_file(demo_file: str | Path, *, sample_rows: int = 3) -> dict[str, Any]:
    return parse_demo_file(demo_file).inspect(sample_rows=sample_rows)


def parse_demo_file(demo_file: str | Path) -> ParsedDemo:
    path = Path(demo_file)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        from awpy import Demo
    except ImportError as exc:
        raise AwpyUnavailableError('awpy is not installed. Install it with: pip install -e ".[demo]"') from exc

    demo = Demo(str(path))
    demo.parse()
    return ParsedDemo(
        path=str(path),
        header=dict(getattr(demo, "header", {}) or {}),
        rounds=_frame_rows(getattr(demo, "rounds", None)),
        kills=_frame_rows(getattr(demo, "kills", None)),
        damages=_frame_rows(getattr(demo, "damages", None)),
    )


def import_demo_to_match_map(
    session: Session,
    demo_file: str | Path,
    *,
    match_id: int,
    map_number: int,
    match_key: str = "local",
    replace_rounds: bool = True,
    import_player_stats: bool = False,
) -> DemoImportResult:
    parsed = parse_demo_file(demo_file)
    match = _get_match(session, match_id, match_key)
    match_map = session.scalar(select(MatchMap).where(MatchMap.match_id == match.id, MatchMap.map_number == map_number))
    if match_map is None:
        match_map = MatchMap(match_id=match.id, map_number=map_number, name=parsed.map_name)
        session.add(match_map)
        session.flush()
    match_map.name = parsed.map_name or match_map.name

    rounds_imported = _import_rounds(session, match_map, parsed.rounds, replace=replace_rounds)
    player_stats_imported = 0
    players_unmapped = 0
    if import_player_stats:
        player_stats_imported, players_unmapped = _import_player_stats(session, match_map, parsed.kills, parsed.damages)
    return DemoImportResult(
        match_id=match.id,
        match_map_id=match_map.id,
        map_name=match_map.name,
        rounds_imported=rounds_imported,
        player_stats_imported=player_stats_imported,
        players_unmapped_to_team=players_unmapped,
    )


def _get_match(session: Session, match_id: int, match_key: str) -> Match:
    if match_key == "local":
        match = session.get(Match, match_id)
    elif match_key == "hltv":
        match = session.scalar(select(Match).where(Match.hltv_match_id == match_id))
    else:
        raise ValueError("match_key must be 'local' or 'hltv'")
    if match is None:
        raise ValueError(f"Match not found for {match_key} id {match_id}")
    return match


def _frame_rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dicts"):
        return [dict(row) for row in frame.to_dicts()]
    if hasattr(frame, "to_dict"):
        data = frame.to_dict(orient="records")
        return [dict(row) for row in data]
    if isinstance(frame, list):
        return [dict(row) for row in frame if isinstance(row, dict)]
    return []


def _columns(rows: list[dict[str, Any]]) -> set[str]:
    return {str(key) for row in rows for key in row}


def _value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _int_value(row: dict[str, Any], *names: str) -> int | None:
    value = _value(row, *names)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_value(row: dict[str, Any], *names: str) -> str | None:
    value = _value(row, *names)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _half_number(round_number: int) -> int:
    if round_number <= 12:
        return 1
    if round_number <= 24:
        return 2
    return 3 + ((round_number - 25) // 6)


def _is_overtime(round_number: int) -> bool:
    return round_number > 24


def _normalize_side(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.upper()
    if upper in {"T", "CT"}:
        return upper
    return None


def _import_rounds(session: Session, match_map: MatchMap, rows: list[dict[str, Any]], *, replace: bool) -> int:
    if replace:
        session.query(Round).filter(Round.match_map_id == match_map.id).delete()
    imported = 0
    for row in rows:
        round_number = _int_value(row, "round_num", "round_number", "round")
        if round_number is None:
            continue
        round_row = None
        if not replace:
            round_row = session.scalar(
                select(Round).where(Round.match_map_id == match_map.id, Round.round_number == round_number)
            )
        if round_row is None:
            round_row = Round(match_map_id=match_map.id, round_number=round_number)
            session.add(round_row)
        overtime = _is_overtime(round_number)
        round_row.half_number = _half_number(round_number)
        round_row.is_overtime = overtime
        round_row.winner_side = _normalize_side(_str_value(row, "winner", "winner_side", "winning_side"))
        round_row.end_method = _str_value(row, "reason", "end_reason", "end_method")
        round_row.is_pistol = is_pistol_round(round_number, overtime)
        imported += 1
    return imported


def _player_key(row: dict[str, Any], role: str) -> tuple[str | None, str | None]:
    steamid = _str_value(row, f"{role}_steamid", f"{role}_steam_id", f"{role}SteamID", f"{role}_xuid")
    name = _str_value(row, f"{role}_name", role)
    if not steamid and not name:
        return None, None
    return steamid or name, name or steamid


def _player_totals(kills: list[dict[str, Any]], damages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"kills": 0, "deaths": 0, "assists": 0, "damage": 0})
    names: dict[str, str] = {}
    for row in kills:
        attacker_key, attacker_name = _player_key(row, "attacker")
        victim_key, victim_name = _player_key(row, "victim")
        assister_key, assister_name = _player_key(row, "assister")
        if attacker_key:
            totals[attacker_key]["kills"] += 1
            names[attacker_key] = attacker_name or attacker_key
        if victim_key:
            totals[victim_key]["deaths"] += 1
            names[victim_key] = victim_name or victim_key
        if assister_key:
            totals[assister_key]["assists"] += 1
            names[assister_key] = assister_name or assister_key
    for row in damages:
        attacker_key, attacker_name = _player_key(row, "attacker")
        damage = _int_value(row, "dmg_health_real", "damage", "dmg_health")
        if attacker_key and damage:
            totals[attacker_key]["damage"] += damage
            names[attacker_key] = attacker_name or attacker_key
    return [
        {
            "external_id": key,
            "name": names.get(key, key),
            "kills": int(value["kills"]),
            "deaths": int(value["deaths"]),
            "assists": int(value["assists"]),
            "damage": int(value["damage"]),
        }
        for key, value in sorted(totals.items(), key=lambda item: (-int(item[1]["kills"]), str(item[0])))
    ]


def _round_summary(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    sides: dict[str, int] = defaultdict(int)
    for row in rounds:
        side = _normalize_side(_str_value(row, "winner", "winner_side", "winning_side"))
        if side:
            sides[side] += 1
    return {"rounds": len(rounds), "t_wins": sides["T"], "ct_wins": sides["CT"], "pistol_rounds": [1, 13] if len(rounds) >= 13 else [1] if rounds else []}


def _save_external_player_map(session: Session, external_id: str, player: Player, name: str | None) -> None:
    row = session.scalar(
        select(ExternalEntityMap).where(
            ExternalEntityMap.provider == "steam",
            ExternalEntityMap.entity_type == "player",
            ExternalEntityMap.external_id == external_id,
        )
    )
    if row is None:
        row = ExternalEntityMap(
            provider="steam",
            entity_type="player",
            external_id=external_id,
            local_table="players",
            local_id=player.id,
            name=name,
        )
        session.add(row)
    row.local_id = player.id
    row.name = name or row.name


def _import_player_stats(session: Session, match_map: MatchMap, kills: list[dict[str, Any]], damages: list[dict[str, Any]]) -> tuple[int, int]:
    totals = _player_totals(kills, damages)
    rounds_count = session.scalar(select(func.count(Round.id)).where(Round.match_map_id == match_map.id)) or 0
    imported = 0
    unmapped = 0
    for row in totals:
        external_id = str(row["external_id"])
        player = session.scalar(select(Player).where(Player.hltv_player_id == stable_negative_id(f"steam-player:{external_id}")))
        if player is None:
            player = Player(hltv_player_id=stable_negative_id(f"steam-player:{external_id}"), nickname=str(row["name"]))
            session.add(player)
            session.flush()
        player.nickname = str(row["name"])
        _save_external_player_map(session, external_id, player, str(row["name"]))
        stat = session.scalar(select(PlayerMapStat).where(PlayerMapStat.match_map_id == match_map.id, PlayerMapStat.player_id == player.id))
        if stat is None:
            stat = PlayerMapStat(match_map_id=match_map.id, player_id=player.id)
            session.add(stat)
        kills_count = int(row["kills"])
        deaths_count = int(row["deaths"])
        stat.kills = kills_count
        stat.deaths = deaths_count
        stat.assists = int(row["assists"])
        stat.kd_diff = kills_count - deaths_count
        stat.kd_ratio = calculate_kd_ratio(kills_count, deaths_count)
        stat.adr = (float(row["damage"]) / rounds_count) if rounds_count else None
        if stat.team_id is None:
            unmapped += 1
        imported += 1
    return imported, unmapped

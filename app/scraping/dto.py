from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RankingTeamDTO:
    rank: int
    hltv_team_id: int
    name: str
    country: str | None = None
    points: int | None = None
    logo_url: str | None = None


@dataclass(frozen=True)
class RankingSnapshotDTO:
    ranking_date: datetime
    source_url: str
    teams: list[RankingTeamDTO]


@dataclass(frozen=True)
class TeamDTO:
    hltv_team_id: int
    name: str
    country: str | None = None


@dataclass(frozen=True)
class MapDTO:
    map_number: int
    name: str
    hltv_mapstats_id: int | None = None
    score_team1: int | None = None
    score_team2: int | None = None
    winner_hltv_team_id: int | None = None
    first_half_team1: int | None = None
    first_half_team2: int | None = None
    second_half_team1: int | None = None
    second_half_team2: int | None = None
    overtime: bool = False


@dataclass(frozen=True)
class RoundDTO:
    map_number: int
    round_number: int
    half_number: int | None
    is_overtime: bool
    winner_hltv_team_id: int | None
    winner_side: str | None
    end_method: str | None = None
    score_team1_after: int | None = None
    score_team2_after: int | None = None
    is_pistol: bool = False


@dataclass(frozen=True)
class PlayerStatDTO:
    map_number: int
    hltv_player_id: int
    nickname: str
    hltv_team_id: int | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    kd_diff: int | None = None
    kd_ratio: float | None = None
    adr: float | None = None
    kast: float | None = None
    rating: float | None = None
    headshot_percentage: float | None = None


@dataclass(frozen=True)
class MatchDTO:
    hltv_match_id: int
    source_url: str
    match_time: datetime | None
    status: str
    team1: TeamDTO | None
    team2: TeamDTO | None
    winner_hltv_team_id: int | None
    event_name: str | None = None
    best_of: int | None = None
    score_team1: int | None = None
    score_team2: int | None = None
    maps: list[MapDTO] = field(default_factory=list)
    rounds: list[RoundDTO] = field(default_factory=list)
    player_stats: list[PlayerStatDTO] = field(default_factory=list)


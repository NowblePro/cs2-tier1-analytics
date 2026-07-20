from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    hltv_team_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str | None] = mapped_column(String(120))
    logo_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    hltv_player_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(255), index=True)
    real_name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(120))
    current_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))


class RankingSnapshot(Base):
    __tablename__ = "ranking_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    teams: Mapped[list["RankingSnapshotTeam"]] = relationship(cascade="all, delete-orphan")


class RankingSnapshotTeam(Base):
    __tablename__ = "ranking_snapshot_teams"
    __table_args__ = (UniqueConstraint("snapshot_id", "rank", name="uq_snapshot_rank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ranking_snapshots.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    hltv_event_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255), index=True)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    hltv_match_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    match_time: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(50), default="completed")
    team1_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    team2_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    best_of: Mapped[int | None] = mapped_column(Integer)
    score_team1: Mapped[int | None] = mapped_column(Integer)
    score_team2: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    maps: Mapped[list["MatchMap"]] = relationship(cascade="all, delete-orphan")


class MatchMap(Base):
    __tablename__ = "match_maps"
    __table_args__ = (UniqueConstraint("match_id", "map_number", name="uq_match_map_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    hltv_mapstats_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    map_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    picked_by_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    score_team1: Mapped[int | None] = mapped_column(Integer)
    score_team2: Mapped[int | None] = mapped_column(Integer)
    first_half_team1: Mapped[int | None] = mapped_column(Integer)
    first_half_team2: Mapped[int | None] = mapped_column(Integer)
    second_half_team1: Mapped[int | None] = mapped_column(Integer)
    second_half_team2: Mapped[int | None] = mapped_column(Integer)
    overtime: Mapped[bool] = mapped_column(Boolean, default=False)


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("match_map_id", "round_number", name="uq_map_round"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_map_id: Mapped[int] = mapped_column(ForeignKey("match_maps.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    half_number: Mapped[int | None] = mapped_column(Integer)
    is_overtime: Mapped[bool] = mapped_column(Boolean, default=False)
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    winner_side: Mapped[str | None] = mapped_column(String(2))
    end_method: Mapped[str | None] = mapped_column(String(80))
    score_team1_after: Mapped[int | None] = mapped_column(Integer)
    score_team2_after: Mapped[int | None] = mapped_column(Integer)
    is_pistol: Mapped[bool] = mapped_column(Boolean, default=False)


class PlayerMapStat(Base):
    __tablename__ = "player_map_stats"
    __table_args__ = (UniqueConstraint("match_map_id", "player_id", name="uq_player_map_stat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_map_id: Mapped[int] = mapped_column(ForeignKey("match_maps.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    kills: Mapped[int | None] = mapped_column(Integer)
    deaths: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    kd_diff: Mapped[int | None] = mapped_column(Integer)
    kd_ratio: Mapped[float | None] = mapped_column(Float)
    adr: Mapped[float | None] = mapped_column(Float)
    kast: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float | None] = mapped_column(Float)
    headshot_percentage: Mapped[float | None] = mapped_column(Float)


class RawPage(Base):
    __tablename__ = "raw_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, index=True)
    page_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(40), default="0.1.0")


class GridRawSeriesState(Base):
    __tablename__ = "grid_raw_series_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    grid_series_id: Mapped[str] = mapped_column(String(120), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source_endpoint: Mapped[str] = mapped_column(String(120), default="series-state")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    has_games: Mapped[bool] = mapped_column(Boolean, default=False)
    has_maps: Mapped[bool] = mapped_column(Boolean, default=False)
    has_players: Mapped[bool] = mapped_column(Boolean, default=False)
    payload_json: Mapped[str] = mapped_column(Text)


class GridSyncCursor(Base):
    __tablename__ = "grid_sync_cursors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    date_from: Mapped[datetime | None] = mapped_column(DateTime)
    date_to: Mapped[datetime | None] = mapped_column(DateTime)
    last_successful_to: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_result_json: Mapped[str | None] = mapped_column(Text)


class GridEntityMap(Base):
    __tablename__ = "grid_entity_maps"
    __table_args__ = (UniqueConstraint("entity_type", "grid_id", name="uq_grid_entity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    grid_id: Mapped[str] = mapped_column(String(160), index=True)
    local_table: Mapped[str | None] = mapped_column(String(80))
    local_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GridStatsSnapshot(Base):
    __tablename__ = "grid_stats_snapshots"
    __table_args__ = (UniqueConstraint("entity_type", "grid_id", "window_name", name="uq_grid_stats_entity_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    grid_id: Mapped[str] = mapped_column(String(160), index=True)
    local_table: Mapped[str | None] = mapped_column(String(80))
    local_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    window_name: Mapped[str] = mapped_column(String(40), index=True)
    source_endpoint: Mapped[str] = mapped_column(String(120), default="statistics-feed")
    payload_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    command: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    pages_requested: Mapped[int] = mapped_column(Integer, default=0)
    new_matches: Mapped[int] = mapped_column(Integer, default=0)
    updated_matches: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped_pages: Mapped[int] = mapped_column(Integer, default=0)
    http_403_429: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    request_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    progress_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class TeamRollingMetric(Base):
    __tablename__ = "team_rolling_metrics"
    __table_args__ = (UniqueConstraint("team_id", "window_name", name="uq_team_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    window_name: Mapped[str] = mapped_column(String(40))
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    match_win_rate: Mapped[float | None] = mapped_column(Float)
    map_win_rate: Mapped[float | None] = mapped_column(Float)
    kd_ratio: Mapped[float | None] = mapped_column(Float)
    t_round_win_rate: Mapped[float | None] = mapped_column(Float)
    ct_round_win_rate: Mapped[float | None] = mapped_column(Float)
    pistol_win_rate: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

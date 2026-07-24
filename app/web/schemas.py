from datetime import datetime

from pydantic import BaseModel, Field


class GridSyncRequest(BaseModel):
    mode: str = Field(
        default="recent",
        pattern=(
            "^(recent|backfill|update|update-all|upcoming|pandascore-upcoming|"
            "pandascore-results|valve-ranking|refresh-live|repair|audit|team|match)$"
        ),
    )
    days: int = Field(default=7, ge=1, le=365)
    date_from: datetime | None = None
    date_to: datetime | None = None
    window_days: int = Field(default=1, ge=1, le=31)
    max_pages: int = Field(default=5, ge=1, le=50)
    max_matches: int = Field(default=30, ge=1, le=5000)
    history_days: int = Field(default=90, ge=0, le=365)
    participant_history_days: int = Field(default=180, ge=30, le=365)
    history_max_pages: int = Field(default=20, ge=1, le=50)
    history_max_matches: int = Field(default=200, ge=0, le=1000)
    top_limit: int = Field(default=50, ge=1, le=100)
    require_top_team: bool = True
    cursor: str = "grid-main"
    dry_run: bool = False
    force_refresh: bool = False
    post_pipeline: bool = True
    refresh_stats: bool = True
    stats_window: str = Field(
        default="LAST_MONTH",
        pattern="^(LAST_WEEK|LAST_MONTH|LAST_3_MONTHS|LAST_6_MONTHS|LAST_YEAR)$",
    )
    team_id: int | None = None
    match_id: int | None = None
    trigger: str = Field(default="manual", pattern="^(manual|automation|recovery)$")


class GridStatsRefreshRequest(BaseModel):
    entity_type: str = Field(default="team", pattern="^(team|player)$")
    window: str = Field(
        default="LAST_MONTH",
        pattern="^(LAST_WEEK|LAST_MONTH|LAST_3_MONTHS|LAST_6_MONTHS|LAST_YEAR)$",
    )
    limit: int = Field(default=30, ge=1, le=100)
    dry_run: bool = False


class BackfillResetRequest(BaseModel):
    date_from: datetime
    date_to: datetime
    cursor: str = "grid-main"


class AutomationRequest(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=30, le=1440)
    upcoming_days: int = Field(default=14, ge=1, le=60)
    results_days: int = Field(default=7, ge=1, le=90)
    top_limit: int = Field(default=50, ge=1, le=100)
    max_matches: int = Field(default=500, ge=1, le=5000)
    refresh_stats: bool = True

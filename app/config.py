from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/cs2.db"
    hltv_base_url: str = "https://www.hltv.org"
    request_delay_min: float = 3
    request_delay_max: float = 5
    request_timeout: float = 30
    max_retries: int = 3
    max_matches_per_run: int = 30
    top_teams_limit: int = 100
    backfill_days: int = 180
    raw_html_dir: Path = Field(default=Path("./data/raw"))
    demo_dir: Path = Field(default=Path("./data/demos"))
    log_level: str = "INFO"
    grid_api_key: str | None = None
    grid_base_url: str = "https://api-op.grid.gg"
    grid_request_limit_per_minute: int = 18
    grid_stats_request_limit_per_minute: int = 9
    pandascore_api_key: str | None = None
    pandascore_base_url: str = "https://api.pandascore.co"
    valve_vrs_github_api_url: str = "https://api.github.com/repos/ValveSoftware/counter-strike_regional_standings"
    job_stale_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.raw_html_dir.mkdir(parents=True, exist_ok=True)
    settings.demo_dir.mkdir(parents=True, exist_ok=True)
    return settings

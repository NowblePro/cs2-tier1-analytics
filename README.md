# CS2 Tier-1 Analytics

Local CS2 analytics app with GRID Open Access ingestion, Stats Feed snapshots, SQLite/PostgreSQL persistence, metrics, validation, and a FastAPI dashboard.

## Setup

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d db
alembic upgrade head
```

Linux or WSL:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
docker compose up -d db
alembic upgrade head
```

For local fixture-only testing you can set `DATABASE_URL=sqlite:///./data/cs2.db`.

## Commands

```bash
python -m app.cli init-db
python -m app.cli scrape-ranking
python -m app.cli scrape-match --match-id 123456
python -m app.cli scrape-recent --days 7
python -m app.cli backfill --from 2026-01-01 --to 2026-07-01 --max-matches 100
python -m app.cli compute-metrics
python -m app.cli validate-data
```

Common scraper flags:

```bash
--dry-run --force-refresh --from-cache --max-pages 10 --max-matches 30
```

The scraper stops on HTTP 403 or 429 and does not attempt to bypass protection.

## GRID Open Access

HLTV may block direct scraping. GRID Open Access is the primary automatic ingestion path:

```powershell
$env:GRID_API_KEY="your-grid-api-key"
python -m app.cli init-db
python -m app.cli grid-sync-recent --days 7 --max-pages 5 --max-matches 30
```

Or put `GRID_API_KEY=...` in your local `.env`. Do not commit real API keys.

`grid-sync-recent` uses the latest ranking snapshot in the database as the top-30 filter. Load a ranking snapshot first, either from saved HTML or another trusted source.

Successful GRID series-state responses are also saved as raw snapshots in `grid_raw_series_states`. This keeps fields that are not mapped into the normalized match/player tables yet.

Backfill the last 30 days for top-50 teams with the default 18 requests/minute GRID throttle:

```powershell
$env:GRID_API_KEY="your-grid-api-key"
python -m app.cli init-db
python -m app.cli grid-backfill --days 30 --window-days 1 --max-pages 20 --max-matches 500 --top-limit 50
```

If the process stops, run the same command again. It resumes from the `grid-main` cursor.

Load only new data since the last successful cursor:

```powershell
python -m app.cli grid-update --fallback-days 7 --max-pages 20 --max-matches 500 --top-limit 50
```

Sync upcoming matches for the next two weeks using the focused top-team policy:

```powershell
python -m app.cli grid-sync-upcoming --days 14 --top-limit 50 --max-pages 20 --max-matches 100 --history-days 90 --history-max-matches 200
```

Upcoming policy:

- save upcoming CS2 series when at least one team is in the latest top-50 ranking snapshot;
- save the opponent too, even when it is outside the ranking snapshot;
- load bounded match history only for teams involved in those upcoming series;
- do not scan or ingest every pro-scene match.

Refresh already saved GRID matches with the current `seriesState` query:

```powershell
python -m app.cli grid-refresh-saved --limit 30
```

Refresh aggregate GRID Stats Feed snapshots for saved numeric GRID ids:

```powershell
python -m app.cli grid-stats-refresh --entity-type team --window LAST_MONTH --limit 30
```

Stats Feed is throttled separately with `GRID_STATS_REQUEST_LIMIT_PER_MINUTE=9` because Open Access currently reports a 10 requests/minute limit for that endpoint.
Player Stats Feed snapshots need GRID player ids. The player ids currently exposed by `seriesState` look like Steam64 ids and are skipped until a reliable GRID-player-id source is mapped.

Normalize GRID/HLTV team aliases after importing new data:

```powershell
python -m app.cli normalize-team-aliases --dry-run
python -m app.cli normalize-team-aliases
python -m app.cli compute-metrics
```

Run the full local post-sync pipeline manually:

```powershell
python -m app.cli run-pipeline --stats-window LAST_MONTH --stats-limit 50
python -m app.cli run-pipeline --no-stats
```

The dashboard sync/update/backfill jobs run the same post-sync pipeline by default:

- normalize team aliases;
- compute metrics;
- refresh team Stats Feed snapshots when enabled;
- validate data and save a JSON report.

Job history is persisted in the `job_runs` table and is visible in the dashboard `Data Status` block and `/api/jobs`.

Estimate a larger backfill before running it:

```powershell
python -m app.cli estimate-backfill --days 30 --window-days 1 --max-pages 20 --max-matches 500
python -m app.cli estimate-backfill --days 180 --window-days 1 --max-pages 20 --max-matches 500
```

The dashboard also shows a live `Backfill Estimate` card based on the current controls.

Inspect GRID schemas. The Stats Feed endpoint is exposed at `https://api-op.grid.gg/statistics-feed/graphql`:

```powershell
python -m app.cli grid-inspect-schema --endpoint stats --types --contains Cs2 --limit 100
python -m app.cli grid-inspect-schema --endpoint stats --type-name Query --limit 100
python -m app.cli grid-inspect-schema --endpoint stats --type-name TeamStatisticsFilter --limit 100
python -m app.cli grid-inspect-schema --endpoint stats --type-name PlayerStatisticsFilter --limit 100
python -m app.cli grid-inspect-schema --endpoint stats --type-name SeriesStatisticsFilter --limit 100
python -m app.cli grid-inspect-schema --endpoint stats --type-name GameStatisticsFilter --limit 100
python -m app.cli grid-inspect-schema --endpoint stats --type-name GameSelection --limit 100
python -m app.cli grid-stats-schema-report
```

## Local Dashboard

The local UI is now a React + TypeScript + Vite app. Source files live in:

```text
cs2-tier1-analytics-frontend/
```

FastAPI serves the compiled build from:

```text
app/web/static/
```

Rebuild the frontend after UI changes:

```powershell
cd cs2-tier1-analytics-frontend
npm install
npm run build
cd ..
Copy-Item cs2-tier1-analytics-frontend\dist\index.html app\web\static\index.html -Force
New-Item -ItemType Directory -Force app\web\static\assets | Out-Null
Copy-Item cs2-tier1-analytics-frontend\dist\assets\* app\web\static\assets -Force
```

Start the local UI. Use `8011` if `8010` is occupied by another project:

```powershell
$env:GRID_API_KEY="your-grid-api-key"
python -m app.cli init-db
python -m uvicorn app.web.main:app --reload --host 127.0.0.1 --port 8011
```

Open `http://127.0.0.1:8011`.

The dashboard can:

- show a compact Analytics/Data Operations UI with Dashboard, Teams, Matches, Upcoming, and Data sections;
- show summary counts, top teams, recent matches, raw GRID snapshot counts, and saved GRID Stats Feed snapshots;
- start a GRID sync as a background job;
- estimate and run bounded GRID backfills from the browser;
- check updates since the last successful cursor;
- refresh team GRID Stats Feed snapshots from the browser;
- show upcoming/scheduled matches;
- show team detail with local rolling metrics, map breakdown, recent matches, and GRID Stats Feed aggregates;
- show team GRID segment stats when Stats Feed provides them;
- compare two selected teams with stronger values highlighted;
- show a rule-based pre-match edge score and confidence in match preview;
- poll job status without freezing the page;
- run a dry-run;
- filter matches by map;
- use a relative range (`Days`) or an exact `From` / `To` datetime range;
- toggle browser-driven auto sync every 30+ minutes while the dashboard tab is open.

During frontend-only development you can run Vite separately:

```powershell
cd cs2-tier1-analytics-frontend
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8011` by default. If your backend runs on another port, set `VITE_API_BASE_URL` or update `vite.config.ts`.

UI redesign prompt for a separate design pass:

```text
docs/ui-redesign-prompt.md
```

## Tests

Unit tests use saved HTML fixtures and do not contact HLTV:

```bash
pytest
```

Live tests are separate and manual:

```bash
pytest -m live
```

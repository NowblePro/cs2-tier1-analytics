Ты выступаешь как senior product designer + frontend architect. Нужно предложить современный, удобный UI/UX дизайн для локального web-приложения CS2 Tier-1 Analytics.

Контекст проекта:
- Это локальный dashboard для аналитики CS2 на данных GRID Open Access.
- Backend уже есть на FastAPI.
- UI сейчас один HTML файл: `app/web/static/index.html`.
- Данные хранятся локально в SQLite/PostgreSQL.
- Пользователь запускает сервер локально: `python -m uvicorn app.web.main:app --reload --host 127.0.0.1 --port 8010`.

Текущие функции UI:
- Summary counters: teams, players, matches, maps, player stats, GRID raw, GRID ids, GRID stats.
- Data Status: cursor, last sync, latest match, latest raw fetch, latest stats fetch, latest validation report, job history.
- Backfill Estimate: windows, estimated requests, ETA.
- Top Teams table.
- Recent Matches table.
- Upcoming Matches table.
- Team detail:
  - local metrics;
  - GRID Stats Feed aggregates;
  - map breakdown;
  - recent matches;
  - GRID segment stats;
  - players.
- Match preview:
  - two teams;
  - event/time/status;
  - recent form;
  - rule-based edge score;
  - map pool comparison;
  - player comparison.
- Compare two selected teams:
  - local metrics;
  - GRID metrics;
  - map pool;
  - stronger values highlighted.
- Controls:
  - Days;
  - From/To datetime;
  - last N matches window: 5/10/20/50;
  - Stats Feed window: LAST_WEEK/LAST_MONTH/LAST_3_MONTHS/LAST_6_MONTHS/LAST_YEAR;
  - Top limit;
  - Max pages;
  - Max matches;
  - map filter;
  - buttons: Sync GRID, Check updates, Backfill, Dry run, Refresh stats, Compute metrics, Validate;
  - toggles: Auto sync, No top filter, Pipeline, Stats after sync.

Available API endpoints:
- `GET /api/summary`
- `GET /api/data-status`
- `GET /api/backfill/estimate`
- `GET /api/jobs`
- `GET /api/teams`
- `GET /api/teams/{team_id}`
- `GET /api/teams/{team_id}/players`
- `GET /api/compare?team1_id=&team2_id=&window=&stats_window=`
- `GET /api/matches`
- `GET /api/matches/{match_id}`
- `GET /api/matches/{match_id}/preview`
- `GET /api/upcoming`
- `GET /api/player-stats`
- `GET /api/players`
- `GET /api/maps`
- `GET /api/grid/stats`
- `POST /api/sync/grid`
- `POST /api/sync/grid-stats`
- `GET /api/sync/grid/jobs/{job_id}`
- `POST /api/metrics/compute`
- `GET /api/validate`

Design goal:
Сделать не landing page, а рабочий аналитический инструмент для регулярного использования. Важно ощущение профессионального sports/esports analytics terminal: плотный, сканируемый, спокойный, без декоративных hero-блоков.

Нужная структура:
1. Верхняя навигация:
   - Dashboard
   - Teams
   - Matches
   - Upcoming
   - Data
2. Dashboard:
   - компактные KPI;
   - последние матчи;
   - предстоящие матчи;
   - data health;
   - active/latest jobs.
3. Teams:
   - таблица топ-команд;
   - фильтр/поиск;
   - при выборе команды открывается profile panel.
4. Team profile:
   - header команды;
   - local metrics;
   - GRID metrics;
   - map pool;
   - segment stats;
   - players;
   - recent form.
5. Upcoming match preview:
   - две команды рядом;
   - rule-based edge score;
   - confidence;
   - comparison table;
   - map pool comparison;
   - players of both teams.
6. Data:
   - sync controls;
   - backfill estimate;
   - job history;
   - validation result;
   - cursor status.

Constraints:
- Не использовать маркетинговый hero.
- Не использовать огромные декоративные карточки.
- Не использовать фиолетово-синий градиент как основную тему.
- UI должен быть плотным, но читаемым.
- Все таблицы должны быть сканируемыми.
- Должны быть понятные состояния: loading, empty, error, stale data.
- Цвета:
  - зеленый только для лучшего/положительного;
  - красный только для ошибок/рисков;
  - основной стиль restrained dark/light analytics dashboard.
- Нужен адаптив под desktop и laptop, mobile вторично.

Что нужно выдать:
1. Информационную архитектуру.
2. Wireframe по секциям.
3. Visual direction: palette, typography, spacing, table style.
4. Component list.
5. Как лучше переразложить текущий single-page HTML.
6. Конкретные рекомендации по UX для job pipeline/backfill.
7. Пример структуры HTML/CSS/JS или React-компонентов, но без полной реализации.

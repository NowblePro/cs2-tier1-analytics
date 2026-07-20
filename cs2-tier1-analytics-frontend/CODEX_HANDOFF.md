# Codex handoff: FastAPI integration

This Vite frontend is already wired to the endpoint paths from the product brief. The default production behavior uses relative `/api/...` URLs. That is the preferred mode when `dist/` is served by the existing FastAPI app.

## First steps

1. Inspect the actual JSON responses from the FastAPI endpoints listed below.
2. Compare them with the tolerant normalizers in `src/services/api.ts` and field aliases used in `src/App.tsx`.
3. Adjust field aliases only where the real schema differs. Do not replace the visual structure unless requested.
4. Keep API calls in `src/services/api.ts`; do not scatter raw base URLs across components.
5. Run `npm run build` and copy the contents of `dist/` to the directory already mounted by FastAPI. Navigation uses URL hashes, so the five main screens do not require an SPA fallback route.

## Endpoint bindings

| UI area | Endpoint |
| --- | --- |
| KPI strip | `GET /api/summary` |
| Data freshness | `GET /api/data-status` |
| Backfill estimate | `GET /api/backfill/estimate` |
| Jobs | `GET /api/jobs` |
| Team ranking | `GET /api/teams` |
| Team profile | `GET /api/teams/{team_id}` |
| Team roster | `GET /api/teams/{team_id}/players` |
| Team comparison | `GET /api/compare` |
| Match archive | `GET /api/matches` |
| Match preview | `GET /api/matches/{match_id}/preview` |
| Upcoming fixtures | `GET /api/upcoming` |
| Map filters | `GET /api/maps` |
| Main GRID sync / backfill / dry run | `POST /api/sync/grid` |
| GRID stats refresh | `POST /api/sync/grid-stats` |
| Metrics compute | `POST /api/metrics/compute` |
| Validation | `GET /api/validate` |

## Important integration checks

- Confirm whether percentages arrive as `0.64` or `64`; the formatter accepts both.
- Confirm whether collections are arrays or objects such as `{items: []}`, `{teams: []}`, `{matches: []}`. Both common forms are handled.
- Confirm the accepted POST payloads for sync, backfill, dry run and metrics compute. The current bodies are intentionally readable placeholders based on the control names.
- The job UI already polls `GET /api/sync/grid/jobs/{job_id}` when a POST response contains `job_id`, `job.id` or `job.job_id`. Adjust the aliases if the backend returns a different envelope.
- The edge score is deliberately labeled rule-based, not a calibrated probability.
- Keep red reserved for errors or explicit risks. A weaker comparison value remains neutral.
- Do not silently substitute demo data in production. Demo mode only runs when `NEXT_PUBLIC_DEMO_MODE=true`.

## FastAPI hosting target

The cleanest production arrangement is same-origin:

```text
http://127.0.0.1:8010/        -> compiled frontend
http://127.0.0.1:8010/api/... -> existing FastAPI endpoints
```

During development, Vite already proxies `/api` to `http://127.0.0.1:8010`. Set `VITE_API_BASE_URL` only when the backend uses a different address. If an explicit cross-origin URL is used, allow the frontend origin in FastAPI CORS middleware.

## Definition of done

- All five navigation views load from the real backend.
- Empty, loading, error and stale states are verified.
- A sync returns a job ID and the UI tracks it until terminal state.
- After a job finishes, summary, data status and job history refresh.
- Backfill estimate is recalculated when scope changes.
- Production build is served by the existing Uvicorn command without a second server.

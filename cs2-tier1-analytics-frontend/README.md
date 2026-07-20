# CS2 Tier-1 Analytics frontend

React + TypeScript + Vite frontend connected to the existing FastAPI endpoint paths.

## Quick start

Run the backend first:

```bash
python -m uvicorn app.web.main:app --reload --host 127.0.0.1 --port 8010
```

Then run the frontend:

```bash
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` calls to `http://127.0.0.1:8010`, so no CORS change is needed for normal local development.

## Demo preview

Open `http://127.0.0.1:5173/?demo=1` to inspect the filled UI without backend data, or set `VITE_DEMO_MODE=true` in `.env.local`.

## Production build

```bash
npm run build
```

The result is written to `dist/`. Copy its contents to the static directory served by FastAPI and add an SPA fallback to `index.html` if hash routing is replaced with path routing. The included UI already uses hash navigation, so a special server fallback is not required for the five main screens.

Read `CODEX_HANDOFF.md` before adapting response field names or POST payloads.

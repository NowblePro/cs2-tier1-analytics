FROM node:22-alpine AS frontend

WORKDIR /frontend
COPY cs2-tier1-analytics-frontend/package.json cs2-tier1-analytics-frontend/package-lock.json ./
RUN npm ci
COPY cs2-tier1-analytics-frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

WORKDIR /app
COPY pyproject.toml ./
COPY app/ ./app/
RUN python -m pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY --from=frontend /frontend/dist/ ./app/web/static/

RUN mkdir -p /app/data/raw /app/data/reports /app/data/backups /app/data/exports \
    && chown -R app:app /app

USER app
EXPOSE 8011

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/healthz', timeout=3)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.web.main:app --host 0.0.0.0 --port 8011"]

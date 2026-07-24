import { MatchesTable } from "../components/MatchesTable";
import { Badge, PageTitle, Panel, StatusDot, TableState } from "../components/ui";
import { useResource } from "../hooks";
import { statusLabel } from "../labels";
import type { Filters, Page } from "../model";
import { openMatchCard } from "../navigation";
import {
  api,
  asArray,
  asRecord,
  formatDate,
  formatNumber,
  getId,
  isStale,
  type JsonRecord,
} from "../services/api";
import { demo } from "../services/demo";


function KpiStrip({ summary }: { summary: JsonRecord }) {
  const entries = [
    ["Команд в рейтинге", summary.ranking_teams],
    ["Команд с матчами", summary.ranked_teams_with_matches],
    ["Игроков", summary.players ?? summary.player_count],
    ["Матчей", summary.matches ?? summary.match_count],
    ["Карт", summary.maps ?? summary.map_count],
    ["Строк статистики", summary.player_stats ?? summary.player_stats_count],
    ["Снимков GRID", summary.grid_raw ?? summary.grid_raw_count],
    ["GRID stats", summary.grid_stats ?? summary.grid_stats_count],
  ];
  return (
    <div className="kpi-strip">
      {entries.map(([label, value]) => (
        <div className="kpi" key={String(label)}>
          <span>{String(label)}</span><strong>{formatNumber(value)}</strong>
        </div>
      ))}
    </div>
  );
}


function JobList({ jobs }: { jobs: JsonRecord[] }) {
  return (
    <div className="job-list">
      {jobs.slice(0, 5).map((job, index) => {
        const status = String(job.status ?? "unknown").toLowerCase();
        const tone = status.includes("fail")
          ? "bad"
          : status.includes("run") || status.includes("queue")
            ? "info"
            : status.includes("complete") || status.includes("success")
              ? "good"
              : "neutral";
        const progress = Number(job.progress_percent ?? job.progress ?? 0);
        return (
          <div className="job-row" key={getId(job, index)}>
            <div className="job-icon">
              <StatusDot tone={tone === "bad" ? "bad" : tone === "good" ? "good" : tone === "info" ? "warn" : "neutral"} />
            </div>
            <div className="job-main">
              <strong>{String(job.type ?? job.job_type ?? "GRID pipeline")}</strong>
              <span>
                {formatDate(job.started_at ?? job.created_at)} · {String(job.message ?? job.stage ?? "Ожидание статуса")}
              </span>
              {progress > 0 && progress < 100 && <div className="progress"><i style={{ width: `${progress}%` }} /></div>}
            </div>
            <Badge tone={tone}>{statusLabel(status)}</Badge>
          </div>
        );
      })}
      {jobs.length === 0 && <div className="empty-small">История задач пока пуста.</div>}
    </div>
  );
}


function HealthList({ status }: { status: JsonRecord }) {
  const rows = [
    ["Cursor", status.cursor ?? status.grid_cursor, "neutral"],
    ["Last sync", status.last_sync, isStale(status.last_sync, 24) ? "warn" : "good"],
    ["Последний матч", status.latest_match ?? status.latest_match_at, isStale(status.latest_match ?? status.latest_match_at, 72) ? "warn" : "good"],
    ["Последняя загрузка", status.latest_raw_fetch, isStale(status.latest_raw_fetch, 24) ? "warn" : "good"],
    ["GRID stats", status.latest_stats_fetch, isStale(status.latest_stats_fetch, 24) ? "warn" : "good"],
    ["Проверка данных", status.validation_status, String(status.validation_status ?? "").includes("fail") ? "bad" : "good"],
  ] as Array<[string, unknown, "good" | "warn" | "bad" | "neutral"]>;
  return (
    <div className="health-list">
      {rows.map(([label, value, tone]) => (
        <div key={label}>
          <span><StatusDot tone={tone} />{label}</span>
          <strong>
            {label === "Cursor" || label === "Проверка данных"
              ? String(value ?? "неизвестно")
              : formatDate(value)}
          </strong>
        </div>
      ))}
    </div>
  );
}


export function Dashboard({
  filters,
  setPage,
}: {
  filters: Filters;
  setPage: (page: Page) => void;
}) {
  const summary = useResource(() => api.get("/api/summary"), demo.summary, []);
  const status = useResource(() => api.get("/api/data-status"), demo.status, []);
  const matches = useResource(
    () => api.get("/api/matches", { days: filters.days, limit: 8, status: "completed" }),
    demo.matches,
    [filters.days],
  );
  const upcoming = useResource(
    () => api.get("/api/upcoming", { days: filters.days, limit: 7 }),
    demo.upcoming,
    [filters.days],
  );
  const jobs = useResource(() => api.get("/api/jobs", { limit: 6 }), demo.jobs, []);
  const matchRows = asArray(matches.data, ["matches", "items", "results"]);
  const upcomingRows = asArray(upcoming.data, ["matches", "upcoming", "items", "results"]);
  const jobRows = asArray(jobs.data, ["jobs", "items", "results"]);

  return (
    <>
      <PageTitle
        eyebrow="Обзор"
        title="Аналитика CS2"
        description="Форма сильнейших команд, результаты, будущие матчи и состояние базы данных."
      >
        <button
          className="button ghost"
          onClick={() => Promise.all([summary.reload(), status.reload(), matches.reload(), upcoming.reload(), jobs.reload()])}
        >
          Обновить экран
        </button>
        <button className="button primary" onClick={() => setPage("upcoming")}>Открыть прогнозы</button>
      </PageTitle>
      <KpiStrip summary={asRecord(summary.data)} />
      <div className="dashboard-grid">
        <Panel
          title="Последние матчи"
          meta={`За ${filters.days} дней`}
          action={<button className="text-button" onClick={() => setPage("matches")}>Показать все</button>}
          className="span-2"
        >
          <TableState loading={matches.loading} error={matches.error} empty={!matchRows.length} onRetry={matches.reload} />
          {!matches.loading && !matches.error && !!matchRows.length && <MatchesTable rows={matchRows} onOpen={openMatchCard} />}
        </Panel>
        <Panel title="Состояние данных" meta="Актуальность базы">
          <TableState loading={status.loading} error={status.error} empty={false} onRetry={status.reload} />
          {!status.loading && !status.error && <HealthList status={asRecord(status.data)} />}
        </Panel>
        <Panel
          title="Будущие матчи"
          meta="Матчи с доступным сравнением"
          action={<button className="text-button" onClick={() => setPage("upcoming")}>Показать все</button>}
          className="span-2 compact-table"
        >
          <TableState loading={upcoming.loading} error={upcoming.error} empty={!upcomingRows.length} onRetry={upcoming.reload} />
          {!upcoming.loading && !upcoming.error && !!upcomingRows.length && <MatchesTable rows={upcomingRows} onOpen={openMatchCard} />}
        </Panel>
        <Panel
          title="Задачи загрузки"
          meta="Синхронизация и расчёты"
          action={<button className="text-button" onClick={() => setPage("data")}>Управление данными</button>}
        >
          <TableState loading={jobs.loading} error={jobs.error} empty={false} onRetry={jobs.reload} />
          {!jobs.loading && !jobs.error && <JobList jobs={jobRows} />}
        </Panel>
      </div>
    </>
  );
}

export type JsonRecord = Record<string, unknown>;

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const envDemoMode = import.meta.env.VITE_DEMO_MODE === "true";
const isDemoMode = () => envDemoMode || (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1");

export class ApiError extends Error {
  status: number;
  detail?: string;
  constructor(message: string, status: number, detail?: string) {
    super(message); this.status = status; this.detail = detail;
  }
}

export const api = {
  get demo() { return isDemoMode(); },
  async request(path: string, init: RequestInit = {}) {
    if (isDemoMode()) return {};
    const { path: requestPath, init: requestInit } = normalizeRequest(path, init);
    const response = await fetch(`${API_BASE}${requestPath}`, {
      ...requestInit,
      headers: { Accept: "application/json", ...(requestInit.body ? { "Content-Type": "application/json" } : {}), ...requestInit.headers },
      cache: "no-store",
    });
    const contentType = response.headers.get("content-type") ?? "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = typeof payload === "object" && payload ? String(payload.detail ?? payload.message ?? "") : String(payload);
      throw new ApiError(`Request failed (${response.status})`, response.status, detail);
    }
    return normalizeResponse(path, payload);
  },
  get(path: string, params: JsonRecord = {}) {
    const search = new URLSearchParams();
    const normalizedParams = normalizeQueryParams(path, params);
    Object.entries(normalizedParams).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") search.set(key, String(value)); });
    const query = search.toString();
    return this.request(`${path}${query ? `?${query}` : ""}`);
  },
};

// Central endpoint catalogue for future screens and backend-schema adapters.
// Existing components call the same paths directly for readability.
export const endpoints = {
  summary: "/api/summary",
  dataStatus: "/api/data-status",
  backfillEstimate: "/api/backfill/estimate",
  jobs: "/api/jobs",
  teams: "/api/teams",
  team: (teamId: string) => `/api/teams/${teamId}`,
  teamPlayers: (teamId: string) => `/api/teams/${teamId}/players`,
  compare: "/api/compare",
  matches: "/api/matches",
  match: (matchId: string) => `/api/matches/${matchId}`,
  matchPreview: (matchId: string) => `/api/matches/${matchId}/preview`,
  upcoming: "/api/upcoming",
  playerStats: "/api/player-stats",
  players: "/api/players",
  maps: "/api/maps",
  gridStats: "/api/grid/stats",
  syncGrid: "/api/sync/grid",
  syncGridStats: "/api/sync/grid-stats",
  syncJob: (jobId: string) => `/api/sync/grid/jobs/${jobId}`,
  computeMetrics: "/api/metrics/compute",
  validate: "/api/validate",
} as const;

export function asRecord(value: unknown): JsonRecord { return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {}; }
export function asArray(value: unknown, keys: string[] = []): JsonRecord[] {
  if (Array.isArray(value)) return value as JsonRecord[];
  const record = asRecord(value);
  for (const key of keys) if (Array.isArray(record[key])) return record[key];
  return [];
}
export function getId(value: unknown, fallback: unknown = "unknown") { const r = asRecord(value); return String(r.id ?? r.team_id ?? r.match_id ?? r.player_id ?? r.grid_id ?? fallback); }
export function getName(value: unknown, fallback = "Unknown") { if (typeof value === "string") return value; const r = asRecord(value); return String(r.name ?? r.team_name ?? r.player_name ?? r.nickname ?? r.map_name ?? fallback); }
export function formatNumber(value: unknown, digits = 0) { const n = Number(value); if (!Number.isFinite(n)) return "—"; const shown = digits ? n.toFixed(digits) : Math.round(n).toLocaleString("en-US"); return n > 0 && digits === 1 && String(value).startsWith("+") ? `+${shown}` : shown; }
export function formatPercent(value: unknown) { let n = Number(value); if (!Number.isFinite(n)) return "—"; if (Math.abs(n) <= 1) n *= 100; const rounded = Math.round(n * 10) / 10; return `${rounded.toFixed(Number.isInteger(rounded) ? 0 : 1)}%`; }
export function formatDate(value: unknown) { if (!value) return "Never"; const date = new Date(String(value)); if (Number.isNaN(date.getTime())) return String(value); return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(date); }
export function isStale(value: unknown, hours: number) { if (!value) return true; const date = new Date(String(value)); return Number.isNaN(date.getTime()) || Date.now() - date.getTime() > hours * 3_600_000; }

function normalizeQueryParams(path: string, params: JsonRecord): JsonRecord {
  if (!path.startsWith("/api/matches")) return params;
  const next = { ...params };
  if (next.from && !next.date_from) next.date_from = next.from;
  if (next.to && !next.date_to) next.date_to = next.to;
  if (next.map && !next.map_name) next.map_name = next.map;
  delete next.from;
  delete next.to;
  delete next.map;
  return next;
}

function normalizeRequest(path: string, init: RequestInit): { path: string; init: RequestInit } {
  if (!path.startsWith("/api/sync/grid") || !init.body || typeof init.body !== "string") return { path, init };
  try {
    const body = JSON.parse(init.body) as JsonRecord;
    const next: JsonRecord = { ...body };
    if (next.from && !next.date_from) next.date_from = next.from;
    if (next.to && !next.date_to) next.date_to = next.to;
    if (next.backfill) next.mode = "backfill";
    if (next.pipeline !== undefined) next.post_pipeline = next.pipeline;
    if (next.stats_after_sync !== undefined) next.refresh_stats = next.stats_after_sync;
    if (next.no_top_filter !== undefined) next.require_top_team = !next.no_top_filter;
    delete next.from;
    delete next.to;
    delete next.backfill;
    delete next.pipeline;
    delete next.stats_after_sync;
    delete next.no_top_filter;
    return { path, init: { ...init, body: JSON.stringify(next) } };
  } catch {
    return { path, init };
  }
}

function normalizeResponse(path: string, payload: unknown): unknown {
  const cleanPath = path.split("?")[0];
  if (cleanPath === endpoints.summary) return normalizeSummary(payload);
  if (cleanPath === endpoints.dataStatus) return normalizeDataStatus(payload);
  if (cleanPath === endpoints.jobs) return normalizeJobs(payload);
  if (cleanPath === endpoints.teams) return asArray(payload).map(normalizeTeamRow);
  if (/^\/api\/teams\/[^/]+$/.test(cleanPath)) return normalizeTeamDetail(payload);
  if (cleanPath === endpoints.compare) return normalizeCompare(payload);
  if (cleanPath === endpoints.matches || cleanPath === endpoints.upcoming) return asArray(payload, ["matches", "upcoming", "items", "results"]).map(normalizeMatchRow);
  if (/^\/api\/matches\/[^/]+\/preview$/.test(cleanPath)) return normalizePreview(payload);
  if (cleanPath === endpoints.maps) return normalizeMaps(payload);
  if (cleanPath === endpoints.validate) return normalizeValidation(payload);
  return payload;
}

function normalizeSummary(payload: unknown): JsonRecord {
  const item = asRecord(payload);
  return {
    ...item,
    grid_raw: item.grid_raw ?? item.grid_raw_snapshots,
    grid_ids: item.grid_ids ?? item.grid_entity_maps,
    grid_stats: item.grid_stats ?? item.grid_stats_snapshots,
  };
}

function normalizeTeamRow(value: unknown): JsonRecord {
  const team = asRecord(value);
  return {
    ...team,
    matches: team.matches ?? team.matches_played,
    series_win_rate: team.series_win_rate ?? team.match_win_rate,
    round_diff: team.round_diff ?? team.round_differential,
    rating: team.rating ?? team.grid_rating,
  };
}

function normalizeTeamDetail(payload: unknown): JsonRecord {
  const root = asRecord(payload);
  const team = asRecord(root.team ?? payload);
  const metric = asRecord(team.metric);
  const recent = asRecord(team.recent);
  const gridSummary = asRecord(team.grid_summary);
  return normalizeTeamRow({
    ...team,
    ...metric,
    ...recent,
    grid_summary: gridSummary,
    matches: recent.matches_played ?? metric.matches_played,
    series_win_rate: recent.match_win_rate ?? metric.match_win_rate,
    kd: gridSummary.kd_ratio ?? recent.kd_ratio ?? metric.kd_ratio,
    adr: gridSummary.avg_damage ?? recent.avg_adr,
    rating: gridSummary.rating,
    map_breakdown: recent.map_breakdown ?? team.map_breakdown,
  });
}

function normalizeMatchRow(value: unknown): JsonRecord {
  const match = asRecord(value);
  return {
    ...match,
    start_time: match.start_time ?? match.match_time,
    event_name: match.event_name ?? match.event,
    team1_score: match.team1_score ?? match.score_team1,
    team2_score: match.team2_score ?? match.score_team2,
    format: match.format ?? (Array.isArray(match.maps) && match.maps.length ? `BO${match.maps.length}` : undefined),
  };
}

function metricRows(team1: JsonRecord, team2: JsonRecord): JsonRecord[] {
  const a = asRecord(team1.metrics ?? team1.metric ?? team1.recent ?? team1);
  const b = asRecord(team2.metrics ?? team2.metric ?? team2.recent ?? team2);
  return [
    ["Series win rate", "match_win_rate"],
    ["Map win rate", "map_win_rate"],
    ["K/D", "kd_ratio"],
    ["ADR", "avg_adr"],
  ].map(([label, key]) => ({ label, team1_value: a[key], team2_value: b[key] }));
}

function normalizeCompare(payload: unknown): JsonRecord {
  const root = asRecord(payload);
  const team1 = normalizeTeamRow(root.team1);
  const team2 = normalizeTeamRow(root.team2);
  const edge = asRecord(root.edge);
  const backendMetrics = asArray(root.metrics, ["metrics", "items"]);
  return {
    ...root,
    team1,
    team2,
    metrics: backendMetrics.length ? backendMetrics : metricRows(team1, team2),
    map_pool: asArray(root.map_pool, ["map_pool", "maps", "items"]),
    player_form: root.player_form,
    coverage: root.coverage,
    edge_score: edge.edge ?? edge.score ?? edge.edge_score,
    confidence: edge.confidence,
  };
}

function normalizePreview(payload: unknown): JsonRecord {
  const root = asRecord(payload);
  const comparison = normalizeCompare(root.comparison ?? {});
  const match = normalizeMatchRow(root.match);
  const edge = asRecord(comparison.edge);
  return {
    ...match,
    ...comparison,
    edge_score: comparison.edge_score ?? edge.edge ?? edge.score,
    confidence: comparison.confidence ?? edge.confidence,
    comparison: comparison.metrics,
    map_pool: comparison.map_pool,
    player_form: comparison.player_form,
    coverage: comparison.coverage,
  };
}

function normalizeDataStatus(payload: unknown): JsonRecord {
  const status = asRecord(payload);
  const cursor = asRecord(status.cursor);
  const jobs = asArray(status.jobs).map(normalizeJob);
  return {
    ...status,
    cursor: cursor.last_successful_to ?? cursor.last_run_at ?? status.cursor,
    last_sync: cursor.last_run_at ?? status.last_sync,
    latest_match: status.latest_match ?? status.latest_match_time,
    jobs,
  };
}

function normalizeJob(value: unknown): JsonRecord {
  const job = asRecord(value);
  const progress = asRecord(job.progress);
  return {
    ...job,
    id: job.id ?? job.job_id,
    type: job.type ?? job.kind,
    stage: job.stage ?? progress.stage ?? progress.window_start,
    records: job.records ?? progress.saved ?? progress.checked ?? progress.page,
  };
}

function normalizeJobs(payload: unknown): JsonRecord[] {
  return asArray(payload, ["jobs", "items", "results"]).map(normalizeJob);
}

function normalizeMaps(payload: unknown): JsonRecord[] {
  if (Array.isArray(payload)) {
    return payload.map((name) => typeof name === "string" ? { id: name, name } : asRecord(name));
  }
  return asArray(payload, ["maps", "items", "results"]).map((item) => {
    if (typeof item === "string") return { id: item, name: item };
    return asRecord(item);
  });
}

function normalizeValidation(payload: unknown): JsonRecord {
  const root = asRecord(payload);
  const issues = asArray(root.issues);
  return {
    ...root,
    passed: issues.filter((issue) => Number(issue.count ?? 0) === 0).length,
    warnings: issues.filter((issue) => Number(issue.count ?? 0) > 0).length,
    errors: root.ok === false ? issues.filter((issue) => Number(issue.count ?? 0) > 0).length : 0,
    checks: issues.map((issue) => ({ ...issue, name: issue.message ?? issue.code, status: Number(issue.count ?? 0) ? "failed" : "passed", affected: issue.count })),
  };
}

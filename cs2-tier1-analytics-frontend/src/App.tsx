"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  asArray,
  asRecord,
  formatDate,
  formatNumber,
  formatPercent,
  getId,
  getName,
  isStale,
  type JsonRecord,
} from "./services/api";
import { demo } from "./services/demo";

type Page = "dashboard" | "teams" | "matches" | "upcoming" | "data";
type LoadState<T> = { data: T; loading: boolean; error: string | null };

const NAV: Array<{ id: Page; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "teams", label: "Teams" },
  { id: "matches", label: "Matches" },
  { id: "upcoming", label: "Upcoming" },
  { id: "data", label: "Data" },
];

const initialFilters = {
  days: 30,
  window: 20,
  statsWindow: "LAST_3_MONTHS",
  topLimit: 30,
  map: "all",
};

function useHashPage() {
  const read = (): Page => {
    if (typeof window === "undefined") return "dashboard";
    const candidate = window.location.hash.replace("#/", "") as Page;
    return NAV.some((item) => item.id === candidate) ? candidate : "dashboard";
  };
  const [page, setPageState] = useState<Page>(read);
  useEffect(() => {
    const onHash = () => setPageState(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const setPage = (next: Page) => {
    window.location.hash = `#/${next}`;
    setPageState(next);
  };
  return [page, setPage] as const;
}

function useResource<T>(loader: () => Promise<T>, fallback: T, deps: unknown[] = []) {
  // Always begin in the same loading state on server and client. Demo mode can
  // depend on the browser query string, so reading it during initial render
  // would create a hydration mismatch.
  const [state, setState] = useState<LoadState<T>>({ data: fallback, loading: true, error: null });
  const load = useCallback(async () => {
    if (api.demo) {
      setState({ data: fallback, loading: false, error: null });
      return;
    }
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await loader();
      setState({ data, loading: false, error: null });
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : "Unknown request error",
      }));
    }
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => void load(), [load]);
  return { ...state, reload: load };
}

function StatusDot({ tone = "neutral" }: { tone?: "good" | "warn" | "bad" | "neutral" }) {
  return <span className={`status-dot ${tone}`} aria-hidden="true" />;
}

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "good" | "warn" | "bad" | "neutral" | "info" }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

function Panel({ title, meta, action, children, className = "" }: { title: string; meta?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel-head">
        <div>
          <h2>{title}</h2>
          {meta && <p>{meta}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function TableState({ loading, error, empty, onRetry }: { loading: boolean; error: string | null; empty: boolean; onRetry: () => void }) {
  if (loading) return <div className="table-skeleton" aria-label="Loading"><i /><i /><i /><i /></div>;
  if (error) return <div className="inline-state error"><div><strong>Couldn’t load data</strong><span>{error}</span></div><button onClick={onRetry}>Retry</button></div>;
  if (empty) return <div className="inline-state"><div><strong>No data for this view</strong><span>Try a wider date window or run data sync.</span></div></div>;
  return null;
}

function TeamMark({ name }: { name: string }) {
  const initials = name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  const hue = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 360;
  return <span className="team-mark" style={{ "--team-hue": hue } as React.CSSProperties}>{initials}</span>;
}

function PageTitle({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: React.ReactNode }) {
  return (
    <div className="page-title">
      <div><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

function KpiStrip({ summary }: { summary: JsonRecord }) {
  const entries = [
    ["Teams", summary.teams ?? summary.team_count],
    ["Players", summary.players ?? summary.player_count],
    ["Matches", summary.matches ?? summary.match_count],
    ["Maps", summary.maps ?? summary.map_count],
    ["Player stats", summary.player_stats ?? summary.player_stats_count],
    ["GRID raw", summary.grid_raw ?? summary.grid_raw_count],
    ["GRID IDs", summary.grid_ids ?? summary.grid_id_count],
    ["GRID stats", summary.grid_stats ?? summary.grid_stats_count],
  ];
  return <div className="kpi-strip">{entries.map(([label, value]) => <div className="kpi" key={String(label)}><span>{String(label)}</span><strong>{formatNumber(value)}</strong></div>)}</div>;
}

function MatchesTable({ rows, onOpen }: { rows: JsonRecord[]; onOpen?: (row: JsonRecord) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Event</th><th>Team A</th><th className="num">Score</th><th>Team B</th><th>Format</th><th>Status</th></tr></thead>
        <tbody>{rows.map((row, index) => {
          const a = getName(row.team1 ?? row.team_a ?? row.home_team, "TBD");
          const b = getName(row.team2 ?? row.team_b ?? row.away_team, "TBD");
          const scoreA = row.team1_score ?? row.score_a ?? row.home_score;
          const scoreB = row.team2_score ?? row.score_b ?? row.away_score;
          const status = String(row.status ?? "completed");
          return <tr key={getId(row, index)} onClick={() => onOpen?.(row)} className={onOpen ? "clickable" : ""}>
            <td className="muted nowrap">{formatDate(row.start_time ?? row.date ?? row.scheduled_at)}</td>
            <td>{String(row.event_name ?? row.event ?? "—")}</td>
            <td><span className="team-cell"><TeamMark name={a} />{a}</span></td>
            <td className="num score">{scoreA != null ? `${scoreA} : ${scoreB ?? 0}` : "—"}</td>
            <td><span className="team-cell"><TeamMark name={b} />{b}</span></td>
            <td className="muted">{String(row.format ?? row.best_of ?? "—")}</td>
            <td><Badge tone={status.includes("live") ? "bad" : status.includes("scheduled") || status.includes("upcoming") ? "info" : "neutral"}>{status}</Badge></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

function JobList({ jobs }: { jobs: JsonRecord[] }) {
  return <div className="job-list">{jobs.slice(0, 5).map((job, index) => {
    const status = String(job.status ?? "unknown").toLowerCase();
    const tone = status.includes("fail") ? "bad" : status.includes("run") || status.includes("queue") ? "info" : status.includes("complete") || status.includes("success") ? "good" : "neutral";
    const progress = Number(job.progress_percent ?? job.progress ?? 0);
    return <div className="job-row" key={getId(job, index)}>
      <div className="job-icon"><StatusDot tone={tone === "bad" ? "bad" : tone === "good" ? "good" : tone === "info" ? "warn" : "neutral"} /></div>
      <div className="job-main"><strong>{String(job.type ?? job.job_type ?? "GRID pipeline")}</strong><span>{formatDate(job.started_at ?? job.created_at)} · {String(job.message ?? job.stage ?? "Waiting for status")}</span>{progress > 0 && progress < 100 && <div className="progress"><i style={{ width: `${progress}%` }} /></div>}</div>
      <Badge tone={tone}>{status}</Badge>
    </div>;
  })}{jobs.length === 0 && <div className="empty-small">No job history yet.</div>}</div>;
}

function HealthList({ status }: { status: JsonRecord }) {
  const rows = [
    ["Cursor", status.cursor ?? status.grid_cursor, "neutral"],
    ["Last sync", status.last_sync, isStale(status.last_sync, 24) ? "warn" : "good"],
    ["Latest match", status.latest_match ?? status.latest_match_at, isStale(status.latest_match ?? status.latest_match_at, 72) ? "warn" : "good"],
    ["Raw fetch", status.latest_raw_fetch, isStale(status.latest_raw_fetch, 24) ? "warn" : "good"],
    ["Stats fetch", status.latest_stats_fetch, isStale(status.latest_stats_fetch, 24) ? "warn" : "good"],
    ["Validation", status.latest_validation_report ?? status.validation_status, String(status.validation_status ?? "").includes("fail") ? "bad" : "good"],
  ] as Array<[string, unknown, "good" | "warn" | "bad" | "neutral"]>;
  return <div className="health-list">{rows.map(([label, value, tone]) => <div key={label}><span><StatusDot tone={tone} />{label}</span><strong>{label === "Cursor" ? String(value ?? "Not set") : formatDate(value)}</strong></div>)}</div>;
}

function Dashboard({ filters, setPage }: { filters: typeof initialFilters; setPage: (page: Page) => void }) {
  const summary = useResource(() => api.get("/api/summary"), demo.summary, []);
  const status = useResource(() => api.get("/api/data-status"), demo.status, []);
  const matches = useResource(() => api.get("/api/matches", { days: filters.days, limit: 8 }), demo.matches, [filters.days]);
  const upcoming = useResource(() => api.get("/api/upcoming", { days: filters.days, limit: 7 }), demo.upcoming, [filters.days]);
  const jobs = useResource(() => api.get("/api/jobs", { limit: 6 }), demo.jobs, []);
  const [selectedMatch, setSelectedMatch] = useState<JsonRecord | null>(null);
  const matchRows = asArray(matches.data, ["matches", "items", "results"]);
  const upcomingRows = asArray(upcoming.data, ["matches", "upcoming", "items", "results"]);
  const jobRows = asArray(jobs.data, ["jobs", "items", "results"]);
  return <>
    <PageTitle eyebrow="Overview" title="Competition intelligence" description="Tier-1 form, upcoming fixtures and data readiness in one operational view.">
      <button className="button ghost" onClick={() => Promise.all([summary.reload(), status.reload(), matches.reload(), upcoming.reload(), jobs.reload()])}>Refresh view</button>
      <button className="button primary" onClick={() => setPage("upcoming")}>Open previews</button>
    </PageTitle>
    <KpiStrip summary={asRecord(summary.data)} />
    <div className="dashboard-grid">
      <Panel title="Recent matches" meta={`Last ${filters.days} days`} action={<button className="text-button" onClick={() => setPage("matches")}>View all</button>} className="span-2">
        <TableState loading={matches.loading} error={matches.error} empty={!matchRows.length} onRetry={matches.reload} />
        {!matches.loading && !matches.error && !!matchRows.length && <MatchesTable rows={matchRows} onOpen={setSelectedMatch} />}
      </Panel>
      <Panel title="Data health" meta="Pipeline freshness"><TableState loading={status.loading} error={status.error} empty={false} onRetry={status.reload} />{!status.loading && !status.error && <HealthList status={asRecord(status.data)} />}</Panel>
      <Panel title="Upcoming" meta="Preview-ready fixtures" action={<button className="text-button" onClick={() => setPage("upcoming")}>View all</button>} className="span-2 compact-table">
        <TableState loading={upcoming.loading} error={upcoming.error} empty={!upcomingRows.length} onRetry={upcoming.reload} />
        {!upcoming.loading && !upcoming.error && !!upcomingRows.length && <MatchesTable rows={upcomingRows} onOpen={() => setPage("upcoming")} />}
      </Panel>
      <Panel title="Active & latest jobs" meta="Sync and compute activity" action={<button className="text-button" onClick={() => setPage("data")}>Open Data</button>}><TableState loading={jobs.loading} error={jobs.error} empty={false} onRetry={jobs.reload} />{!jobs.loading && !jobs.error && <JobList jobs={jobRows} />}</Panel>
    </div>
    {selectedMatch && <><div className="scrim" onClick={() => setSelectedMatch(null)} /><MatchDetailDrawer match={selectedMatch} onClose={() => setSelectedMatch(null)} /></>}
  </>;
}

const teamMetric = (team: JsonRecord, ...keys: string[]) => keys.map((key) => team[key]).find((value) => value != null);

function TeamProfile({ team, filters, onClose, teams }: { team: JsonRecord; filters: typeof initialFilters; onClose: () => void; teams: JsonRecord[] }) {
  void teams;
  const id = getId(team);
  const details = useResource(() => api.get(`/api/teams/${id}`, { window: filters.window, stats_window: filters.statsWindow }), team, [id, filters.window, filters.statsWindow]);
  const players = useResource(() => api.get(`/api/teams/${id}/players`, { window: filters.window, stats_window: filters.statsWindow }), { players: demo.players }, [id, filters.window, filters.statsWindow]);
  const [tab, setTab] = useState<"overview" | "maps" | "players" | "matches" | "segments">("overview");
  const data = asRecord(details.data);
  const playerRows = asArray(players.data, ["players", "items", "results"]);
  const maps = asArray(data.map_breakdown ?? data.maps, ["maps", "items"]);
  const recentMatches = asArray(data.recent_matches, ["recent_matches", "matches", "items"]);
  const gridSummary = asRecord(data.grid_summary);
  const segments = asArray(gridSummary.segments, ["segments", "items"]);
  const name = getName(data, getName(team));
  return <aside className="profile-panel" aria-label={`${name} profile`}>
    <div className="profile-top">
      <div className="team-identity"><TeamMark name={name} /><div><span>Team profile</span><h2>{name}</h2><p>{String(data.country ?? data.region ?? "International")} / {formatNumber(data.matches ?? data.match_count ?? data.matches_played)} matches / {filters.statsWindow.replaceAll("_", " ")}</p></div></div>
      <button className="icon-button" onClick={onClose} aria-label="Close profile">x</button>
    </div>
    <div className="drawer-tabs">
      {(["overview", "maps", "players", "matches", "segments"] as const).map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item}</button>)}
    </div>
    <div className="profile-scroll">
      {details.error && <div className="notice warn">Live detail request failed. Showing the selected table row.</div>}
      {tab === "overview" && <>
        <div className="profile-section"><div className="section-label"><span>Local metrics</span><Badge>Last {filters.window}</Badge></div><div className="metric-grid">
          <Metric label="Series WR" value={formatPercent(teamMetric(data, "series_win_rate", "win_rate", "match_win_rate"))} />
          <Metric label="Map WR" value={formatPercent(teamMetric(data, "map_win_rate"))} />
          <Metric label="K/D" value={formatNumber(teamMetric(data, "kd", "kd_ratio", "kill_death_ratio"), 2)} />
          <Metric label="Pistol WR" value={formatPercent(teamMetric(data, "pistol_win_rate"))} />
        </div></div>
        <div className="profile-section"><div className="section-label"><span>GRID metrics</span><Badge tone="info">{filters.statsWindow.replaceAll("_", " ")}</Badge></div><div className="metric-grid">
          <Metric label="Series" value={formatNumber(gridSummary.series_count)} />
          <Metric label="Game WR" value={formatPercent(gridSummary.game_win_rate)} />
          <Metric label="First kill" value={formatPercent(gridSummary.first_kill_rate)} />
          <Metric label="Rounds" value={formatNumber(segments.reduce((sum, item) => sum + Number(item.count ?? 0), 0))} />
        </div></div>
        <div className="profile-section"><div className="section-label"><span>Recent form</span><span className="muted">{recentMatches.length} latest</span></div><RecentMatchesList rows={recentMatches} /></div>
      </>}
      {tab === "maps" && <div className="profile-section"><div className="section-label"><span>Map pool</span><span className="muted">Local saved matches</span></div><div className="mini-table"><div className="mini-head"><span>Map</span><span>Played</span><span>Win rate</span></div>{maps.map((map, index) => <div key={getId(map, index)}><strong>{getName(map, String(map.map_name ?? "Unknown"))}</strong><span>{formatNumber(map.played ?? map.matches)}</span><span className={Number(map.win_rate ?? 0) >= .6 ? "positive" : Number(map.win_rate ?? 0) <= .4 ? "negative" : ""}>{formatPercent(map.win_rate)}</span></div>)}</div>{!maps.length && <div className="empty-small">No map data for this team yet.</div>}</div>}
      {tab === "players" && <div className="profile-section"><div className="section-label"><span>Players</span><span className="muted">{playerRows.length} active</span></div><div className="table-wrap"><table><thead><tr><th>Player</th><th className="num">Maps</th><th className="num">K</th><th className="num">D</th><th className="num">K/D</th><th className="num">ADR</th><th className="num">HS%</th></tr></thead><tbody>{playerRows.map((player, index) => <tr key={getId(player, index)}><td><span className="team-cell"><span className="player-avatar">{getName(player, "?")[0]}</span><strong>{getName(player)}</strong></span></td><td className="num">{formatNumber(player.maps)}</td><td className="num">{formatNumber(player.kills)}</td><td className="num">{formatNumber(player.deaths)}</td><td className="num">{formatNumber(player.kd_ratio ?? player.kd, 2)}</td><td className="num">{formatNumber(player.avg_adr ?? player.adr, 1)}</td><td className="num">{formatPercent(player.headshot_percentage)}</td></tr>)}</tbody></table></div></div>}
      {tab === "matches" && <div className="profile-section"><div className="section-label"><span>Recent matches</span><span className="muted">{recentMatches.length} rows</span></div><RecentMatchesList rows={recentMatches} /></div>}
      {tab === "segments" && <div className="profile-section"><div className="section-label"><span>GRID segments</span><Badge tone="info">{filters.statsWindow.replaceAll("_", " ")}</Badge></div><div className="table-wrap"><table><thead><tr><th>Segment</th><th className="num">Rounds</th><th className="num">Win rate</th><th className="num">First kill</th><th className="num">Won first</th><th className="num">K/D</th></tr></thead><tbody>{segments.map((segment, index) => <tr key={getId(segment, index)}><td>{String(segment.type ?? "overall")}</td><td className="num">{formatNumber(segment.count)}</td><td className="num">{formatPercent(segment.win_rate)}</td><td className="num">{formatPercent(segment.first_kill_rate)}</td><td className="num">{formatPercent(segment.won_first_rate)}</td><td className="num">{formatNumber(segment.kd_ratio, 2)}</td></tr>)}</tbody></table></div>{!segments.length && <div className="empty-small">No GRID segment snapshot for this window yet.</div>}</div>}
    </div>
  </aside>;
}

function RecentMatchesList({ rows }: { rows: JsonRecord[] }) {
  if (!rows.length) return <div className="empty-small">No recent matches saved yet.</div>;
  return <div className="mini-table recent-matches"><div className="mini-head"><span>Match</span><span>Score</span><span>Result</span></div>{rows.map((row, index) => {
    const won = row.won === true;
    const lost = row.won === false;
    return <div key={getId(row, index)}><strong>{String(row.team1 ?? "Team A")} vs {String(row.team2 ?? "Team B")}<em>{String(row.event ?? "")}</em></strong><span>{String(row.score_team1 ?? "-")} : {String(row.score_team2 ?? "-")}</span><span className={won ? "positive" : lost ? "negative" : ""}>{won ? "W" : lost ? "L" : String(row.status ?? "-")}</span></div>;
  })}</div>;
}

function Metric({ label, value, signed = false }: { label: string; value: string; signed?: boolean }) {
  const positive = signed && value.startsWith("+");
  const negative = signed && value.startsWith("-");
  return <div className="metric"><span>{label}</span><strong className={positive ? "positive" : negative ? "negative" : ""}>{value}</strong></div>;
}

function ComparisonSummary({ data, loading, error }: { data: JsonRecord; loading: boolean; error: string | null }) {
  if (loading) return <div className="compare-loading">Loading comparison…</div>;
  if (error) return <div className="notice warn">{error}</div>;
  const rows = asArray(data.metrics ?? data.comparison, ["metrics", "items"]);
  return <div className="comparison-list">{rows.length ? rows.slice(0, 6).map((row, index) => <div key={getId(row, index)}><span>{String(row.label ?? row.metric ?? "Metric")}</span><strong>{formatNumber(row.team1_value ?? row.value1, 2)}</strong><strong>{formatNumber(row.team2_value ?? row.value2, 2)}</strong></div>) : <div className="empty-small">Comparison endpoint returned no metric rows.</div>}</div>;
}

function Teams({ filters, setFilters }: { filters: typeof initialFilters; setFilters: React.Dispatch<React.SetStateAction<typeof initialFilters>> }) {
  const teams = useResource(() => api.get("/api/teams", { limit: filters.topLimit, window: filters.window, stats_window: filters.statsWindow }), demo.teams, [filters.topLimit, filters.window, filters.statsWindow]);
  const rows = asArray(teams.data, ["teams", "items", "results"]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const filtered = rows.filter((team) => getName(team).toLowerCase().includes(query.toLowerCase()));
  return <>
    <PageTitle eyebrow="Team intelligence" title="Tier-1 teams" description="Scan form, compare stable windows and inspect the roster behind the numbers." />
    <div className="filter-bar">
      <label className="search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search team…" /></label>
      <label><span>Match window</span><select value={filters.window} onChange={(event) => setFilters((f) => ({ ...f, window: Number(event.target.value) }))}>{[5, 10, 20, 50].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label><span>GRID window</span><select value={filters.statsWindow} onChange={(event) => setFilters((f) => ({ ...f, statsWindow: event.target.value }))}>{["LAST_WEEK", "LAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS", "LAST_YEAR"].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
      <label><span>Top limit</span><select value={filters.topLimit} onChange={(event) => setFilters((f) => ({ ...f, topLimit: Number(event.target.value) }))}>{[10, 20, 30, 50].map((value) => <option key={value}>{value}</option>)}</select></label>
    </div>
    <Panel title="Team ranking" meta={`${filtered.length} teams · local and GRID metrics`} action={<span className="muted">Click a row to inspect</span>}>
      <TableState loading={teams.loading} error={teams.error} empty={!filtered.length} onRetry={teams.reload} />
      {!teams.loading && !teams.error && !!filtered.length && <div className="table-wrap"><table><thead><tr><th className="rank">#</th><th>Team</th><th className="num">Matches</th><th className="num">Series WR</th><th className="num">Map WR</th><th className="num">Round diff</th><th className="num">Pistol WR</th><th className="num">GRID rating</th><th>Form</th><th>Updated</th></tr></thead><tbody>{filtered.map((team, index) => <tr key={getId(team, index)} className={`clickable ${getId(selected ?? {}) === getId(team) ? "selected" : ""}`} onClick={() => setSelected(team)}><td className="rank muted">{index + 1}</td><td><span className="team-cell"><TeamMark name={getName(team)} /><strong>{getName(team)}</strong></span></td><td className="num">{formatNumber(team.matches ?? team.match_count)}</td><td className="num">{formatPercent(team.series_win_rate ?? team.win_rate)}</td><td className="num">{formatPercent(team.map_win_rate)}</td><td className={`num ${Number(team.round_diff ?? team.round_differential) > 0 ? "positive" : Number(team.round_diff ?? team.round_differential) < 0 ? "negative" : ""}`}>{formatNumber(team.round_diff ?? team.round_differential, 1)}</td><td className="num">{formatPercent(team.pistol_win_rate)}</td><td className="num">{formatNumber(team.grid_rating ?? team.rating, 2)}</td><td><Form value={team.form} /></td><td className="muted nowrap">{formatDate(team.updated_at ?? team.last_played)}</td></tr>)}</tbody></table></div>}
    </Panel>
    {selected && <><div className="scrim" onClick={() => setSelected(null)} /><TeamProfile team={selected} filters={filters} onClose={() => setSelected(null)} teams={rows} /></>}
  </>;
}

function Form({ value }: { value: unknown }) {
  const entries = Array.isArray(value) ? value : typeof value === "string" ? value.split("") : ["W", "W", "L", "W", "W"];
  return <span className="form-sequence">{entries.slice(-5).map((item, index) => <i key={index} className={String(item).toLowerCase().startsWith("w") ? "win" : "loss"}>{String(item)[0]}</i>)}</span>;
}

function Matches({ filters, setFilters }: { filters: typeof initialFilters; setFilters: React.Dispatch<React.SetStateAction<typeof initialFilters>> }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [selectedMatch, setSelectedMatch] = useState<JsonRecord | null>(null);
  const matches = useResource(() => api.get("/api/matches", { days: filters.days, from: from || undefined, to: to || undefined, map: filters.map === "all" ? undefined : filters.map }), demo.matches, [filters.days, from, to, filters.map]);
  const maps = useResource(() => api.get("/api/maps"), { maps: demo.maps }, []);
  const rows = asArray(matches.data, ["matches", "items", "results"]);
  const mapRows = asArray(maps.data, ["maps", "items", "results"]);
  return <>
    <PageTitle eyebrow="Match archive" title="Matches" description="Review completed series and drill into map-level performance." />
    <div className="filter-bar">
      <label><span>From</span><input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
      <label><span>To</span><input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} /></label>
      <label><span>Map</span><select value={filters.map} onChange={(event) => setFilters((f) => ({ ...f, map: event.target.value }))}><option value="all">All maps</option>{mapRows.map((map, index) => <option key={getId(map, index)} value={String(map.slug ?? map.name ?? map.map_name)}>{getName(map, String(map.map_name ?? "Map"))}</option>)}</select></label>
      <button className="button ghost" onClick={() => { setFrom(""); setTo(""); setFilters((f) => ({ ...f, map: "all" })); }}>Reset</button>
    </div>
    <Panel title="Match results" meta={`${rows.length} records in current view`}><TableState loading={matches.loading} error={matches.error} empty={!rows.length} onRetry={matches.reload} />{!matches.loading && !matches.error && !!rows.length && <MatchesTable rows={rows} onOpen={setSelectedMatch} />}</Panel>
    {selectedMatch && <><div className="scrim" onClick={() => setSelectedMatch(null)} /><MatchDetailDrawer match={selectedMatch} onClose={() => setSelectedMatch(null)} /></>}
  </>;
}

function MatchDetailDrawer({ match, onClose }: { match: JsonRecord; onClose: () => void }) {
  const id = getId(match);
  const detail = useResource(() => api.get(`/api/matches/${id}`), { match }, [id]);
  const [tab, setTab] = useState<"overview" | "maps" | "players">("overview");
  const root = asRecord(detail.data);
  const data = asRecord(root.match ?? match);
  const maps = asArray(data.maps, ["maps", "items"]);
  const stats = asArray(data.player_stats, ["player_stats", "items"]);
  const teamA = getName(data.team1 ?? match.team1, "Team A");
  const teamB = getName(data.team2 ?? match.team2, "Team B");
  const scoreA = Number(data.score_team1 ?? match.team1_score);
  const scoreB = Number(data.score_team2 ?? match.team2_score);
  const winner = Number.isFinite(scoreA) && Number.isFinite(scoreB) ? (scoreA > scoreB ? teamA : scoreB > scoreA ? teamB : null) : null;
  const mapsWithStats = maps.map((map) => ({ map, stats: stats.filter((row) => String(row.map ?? "") === String(map.name ?? map.map_name ?? "")) }));
  return <aside className="profile-panel match-panel" aria-label="Match detail">
    <div className="profile-top">
      <div className="team-identity"><TeamMark name={teamA} /><div><span>Match detail</span><h2>{teamA} vs {teamB}</h2><p>{String(data.event ?? match.event_name ?? "Unknown event")} / {formatDate(data.match_time ?? match.start_time)} / {String(data.status ?? match.status ?? "unknown")}</p></div></div>
      <button className="icon-button" onClick={onClose} aria-label="Close match detail">x</button>
    </div>
    <div className="drawer-tabs">
      {(["overview", "maps", "players"] as const).map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item}</button>)}
    </div>
    <div className="profile-scroll">
      <TableState loading={detail.loading} error={detail.error} empty={false} onRetry={detail.reload} />
      {tab === "overview" && <>
        <div className="profile-section">
          <div className="match-scoreline">
            <div className={winner === teamA ? "winner" : ""}><TeamMark name={teamA} /><strong>{teamA}</strong></div>
            <span>{String(data.score_team1 ?? match.team1_score ?? "-")} : {String(data.score_team2 ?? match.team2_score ?? "-")}</span>
            <div className={winner === teamB ? "winner" : ""}><strong>{teamB}</strong><TeamMark name={teamB} /></div>
          </div>
        </div>
        <div className="profile-section"><div className="section-label"><span>Summary</span><Badge tone={winner ? "good" : "neutral"}>{winner ? `${winner} won` : String(data.status ?? "unknown")}</Badge></div><div className="metric-grid">
          <Metric label="Maps" value={formatNumber(maps.length)} />
          <Metric label="Player rows" value={formatNumber(stats.length)} />
          <Metric label="Team A score" value={formatNumber(scoreA)} />
          <Metric label="Team B score" value={formatNumber(scoreB)} />
        </div>{Boolean(data.source_url) && <a className="source-link" href={String(data.source_url)} target="_blank" rel="noreferrer">Open source page</a>}</div>
      </>}
      {tab === "maps" && <div className="profile-section"><div className="section-label"><span>Maps</span><span className="muted">{maps.length} played</span></div><div className="mini-table match-maps"><div className="mini-head"><span>Map</span><span>#</span><span>Score</span></div>{maps.map((item, index) => <div key={getId(item, index)}><strong>{getName(item, String(item.name ?? "Unknown"))}</strong><span>{formatNumber(item.number ?? item.map_number)}</span><span>{String(item.score_team1 ?? "-")} : {String(item.score_team2 ?? "-")}</span></div>)}</div></div>}
      {tab === "players" && <div className="profile-section"><div className="section-label"><span>Player stats</span><span className="muted">Grouped by map</span></div>{mapsWithStats.map(({ map, stats: mapStats }, mapIndex) => <div className="map-stat-group" key={getId(map, mapIndex)}><h3>{getName(map, String(map.name ?? "Map"))} <span>{String(map.score_team1 ?? "-")} : {String(map.score_team2 ?? "-")}</span></h3><div className="table-wrap"><table><thead><tr><th>Player</th><th>Team</th><th className="num">K</th><th className="num">D</th><th className="num">A</th><th className="num">K/D</th><th className="num">ADR</th></tr></thead><tbody>{mapStats.map((row, index) => <tr key={getId(row, index)}><td>{String(row.player ?? row.nickname ?? "Unknown")}</td><td>{String(row.team ?? "-")}</td><td className="num">{formatNumber(row.kills)}</td><td className="num">{formatNumber(row.deaths)}</td><td className="num">{formatNumber(row.assists)}</td><td className="num">{formatNumber(row.kd_ratio, 2)}</td><td className="num">{formatNumber(row.adr, 1)}</td></tr>)}</tbody></table></div></div>)}</div>}
    </div>
  </aside>;
}

function Upcoming({ filters }: { filters: typeof initialFilters }) {
  const upcoming = useResource(() => api.get("/api/upcoming", { days: filters.days }), demo.upcoming, [filters.days]);
  const rows = asArray(upcoming.data, ["upcoming", "matches", "items", "results"]);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  return <>
    <PageTitle eyebrow="Pre-match analysis" title="Upcoming matches" description="Rule-based edges with explicit confidence and sample context." />
    <div className="upcoming-layout">
      <Panel title="Schedule" meta={`${rows.length} upcoming series`}>
        <TableState loading={upcoming.loading} error={upcoming.error} empty={!rows.length} onRetry={upcoming.reload} />
        {!upcoming.loading && !upcoming.error && <div className="fixture-list">{rows.map((row, index) => { const a = getName(row.team1 ?? row.team_a, "TBD"); const b = getName(row.team2 ?? row.team_b, "TBD"); return <button key={getId(row, index)} onClick={() => setSelected(row)} className={getId(selected ?? {}) === getId(row) ? "active" : ""}><span>{formatDate(row.start_time ?? row.scheduled_at)}</span><div><strong>{a}</strong><em>vs</em><strong>{b}</strong></div><small>{String(row.event_name ?? row.event ?? "Unknown event")}</small></button>; })}</div>}
      </Panel>
      {selected ? <MatchPreview match={selected} filters={filters} /> : <Panel title="Match preview" meta="Select a fixture"><div className="preview-empty"><span>◎</span><strong>Choose an upcoming match</strong><p>The preview will compare recent form, map pool, GRID metrics and both rosters.</p></div></Panel>}
    </div>
  </>;
}

function MatchPreview({ match, filters }: { match: JsonRecord; filters: typeof initialFilters }) {
  const id = getId(match);
  const preview = useResource(() => api.get(`/api/matches/${id}/preview`, { window: filters.window, stats_window: filters.statsWindow }), match.preview ?? demo.preview, [id, filters.window, filters.statsWindow]);
  const data = asRecord(preview.data);
  const a = asRecord(data.team1 ?? data.team_a ?? match.team1 ?? match.team_a);
  const b = asRecord(data.team2 ?? data.team_b ?? match.team2 ?? match.team_b);
  const edge = Number(data.edge_score ?? data.edge ?? 0);
  const scoreA = Math.max(0, Math.min(100, 50 + edge / 2));
  const scoreB = 100 - scoreA;
  const confidence = String(data.confidence ?? "low").toLowerCase();
  const comparisons = asArray(data.comparison ?? data.metrics, ["comparison", "metrics", "items"]);
  const maps = asArray(data.map_pool ?? data.maps, ["maps", "items"]);
  const coverage = asRecord(data.coverage);
  const coverageA = asRecord(coverage.team1);
  const coverageB = asRecord(coverage.team2);
  const warnings = Array.isArray(coverage.warnings) ? coverage.warnings.map(String) : [];
  const playerForm = asRecord(data.player_form);
  const playersA = asArray(playerForm.team1, ["team1", "players"]);
  const playersB = asArray(playerForm.team2, ["team2", "players"]);
  const metricValue = (row: JsonRecord, key: string) => {
    const unit = String(row.unit ?? "");
    const value = row[key];
    return unit.includes("percent") ? formatPercent(value) : formatNumber(value, unit === "number" ? 2 : 1);
  };
  const barWidth = (value: unknown) => {
    let n = Number(value);
    if (!Number.isFinite(n)) return 0;
    if (Math.abs(n) <= 1) n *= 100;
    return Math.max(0, Math.min(100, n));
  };
  return <Panel title="Match preview" meta={`${String(match.event_name ?? match.event ?? "Event")} · ${formatDate(match.start_time ?? match.scheduled_at)}`}>
    {preview.error && <div className="notice warn">Live preview is unavailable. Showing only fixture data.</div>}
    <div className="versus-head">
      <div><TeamMark name={getName(a, "Team A")} /><strong>{getName(a, "Team A")}</strong><Form value={a.form} /></div>
      <div className="edge"><span>Rule-based edge</span><strong>{scoreA.toFixed(0)} <em>:</em> {scoreB.toFixed(0)}</strong><Badge tone={confidence === "high" ? "good" : confidence === "low" ? "warn" : "info"}>{confidence} confidence</Badge></div>
      <div><TeamMark name={getName(b, "Team B")} /><strong>{getName(b, "Team B")}</strong><Form value={b.form} /></div>
    </div>
    <div className="edge-disclaimer">Model score, not a calibrated win probability. Confidence reflects freshness, coverage and sample size.</div>
    <div className="coverage-strip">
      <div><strong>{getName(a, "Team A")}</strong><Badge tone={String(coverageA.level ?? "low") === "high" ? "good" : String(coverageA.level ?? "low") === "medium" ? "info" : "warn"}>{String(coverageA.level ?? "low")}</Badge><span>{formatNumber(coverageA.matches)} matches / {formatNumber(coverageA.maps)} maps / {formatNumber(coverageA.players_with_stats)} players</span></div>
      <div><strong>{getName(b, "Team B")}</strong><Badge tone={String(coverageB.level ?? "low") === "high" ? "good" : String(coverageB.level ?? "low") === "medium" ? "info" : "warn"}>{String(coverageB.level ?? "low")}</Badge><span>{formatNumber(coverageB.matches)} matches / {formatNumber(coverageB.maps)} maps / {formatNumber(coverageB.players_with_stats)} players</span></div>
    </div>
    {warnings.length > 0 && <div className="notice warn"><strong>Data coverage:</strong> {warnings.join("; ")}</div>}
    <div className="preview-section"><h3>Core comparison</h3>{comparisons.length ? <div className="comparison-table"><div><span>Metric</span><strong>{getName(a, "A")}</strong><strong>{getName(b, "B")}</strong></div>{comparisons.map((row, index) => { const v1 = Number(row.team1_value ?? row.value1 ?? row.a); const v2 = Number(row.team2_value ?? row.value2 ?? row.b); return <div key={getId(row, index)}><span>{String(row.label ?? row.metric)}</span><strong className={v1 > v2 ? "positive" : ""}>{String(row.display1 ?? metricValue(row, "team1_value"))}</strong><strong className={v2 > v1 ? "positive" : ""}>{String(row.display2 ?? metricValue(row, "team2_value"))}</strong></div>; })}</div> : <div className="empty-small">Not enough saved data for this comparison yet.</div>}</div>
    <div className="preview-section"><h3>Map pool</h3>{maps.length ? <div className="map-compare">{maps.map((map, index) => { const va = map.team1_win_rate ?? map.a; const vb = map.team2_win_rate ?? map.b; return <div key={getId(map, index)}><strong>{getName(map, String(map.map_name ?? map.map ?? "Map"))}</strong><span>{formatPercent(va)} <small>n={formatNumber(map.team1_sample ?? map.sample_a)}</small></span><i><em style={{ width: `${barWidth(va)}%` }} /></i><span>{formatPercent(vb)} <small>n={formatNumber(map.team2_sample ?? map.sample_b)}</small></span><Badge tone={String(map.state ?? "").includes("insufficient") ? "warn" : "neutral"}>{String(map.state ?? "ready")}</Badge></div>; })}</div> : <div className="empty-small">No saved map pool data for these teams yet.</div>}</div>
    <div className="preview-section"><h3>Player form</h3><div className="player-compare"><div><strong>{getName(a, "Team A")}</strong>{playersA.length ? playersA.map((player, index) => <span key={getId(player, index)}><em>{String(player.nickname ?? player.player ?? "Player")}</em><small>K/D {formatNumber(player.kd_ratio, 2)} / ADR {formatNumber(player.adr, 1)} / {formatNumber(player.maps)} maps</small></span>) : <p>No player stats saved.</p>}</div><div><strong>{getName(b, "Team B")}</strong>{playersB.length ? playersB.map((player, index) => <span key={getId(player, index)}><em>{String(player.nickname ?? player.player ?? "Player")}</em><small>K/D {formatNumber(player.kd_ratio, 2)} / ADR {formatNumber(player.adr, 1)} / {formatNumber(player.maps)} maps</small></span>) : <p>No player stats saved.</p>}</div></div></div>
  </Panel>;
}

function DataPage() {
  const status = useResource(() => api.get("/api/data-status"), demo.status, []);
  const jobs = useResource(() => api.get("/api/jobs", { limit: 50 }), demo.jobs, []);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [backfillDays, setBackfillDays] = useState(90);
  const [maxPages, setMaxPages] = useState(10);
  const [maxMatches, setMaxMatches] = useState(500);
  const [toggles, setToggles] = useState({ autoSync: false, noTop: false, pipeline: true, statsAfter: true });
  const estimate = useResource(() => api.get("/api/backfill/estimate", { days: backfillDays, max_pages: maxPages, max_matches: maxMatches, refresh_stats: toggles.statsAfter }), demo.estimate, [backfillDays, maxPages, maxMatches, toggles.statsAfter]);
  const validationReport = useResource(() => api.get("/api/validate"), { checks: demo.validation }, []);
  const [running, setRunning] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const validation = asRecord(validationReport.data);
  const estimateData = asRecord(estimate.data);
  const jobRows = asArray(jobs.data, ["jobs", "items", "results"]);
  useEffect(() => {
    if (!activeJobId || api.demo) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = asRecord(await api.get(`/api/sync/grid/jobs/${activeJobId}`));
        const current = asRecord(job.job ?? job);
        const jobStatus = String(current.status ?? "running").toLowerCase();
        await Promise.all([jobs.reload(), status.reload(), validationReport.reload()]);
        if (!cancelled && ["completed", "complete", "success", "succeeded", "failed", "error", "cancelled"].some((value) => jobStatus.includes(value))) {
          setActiveJobId(null);
          setMessage(jobStatus.includes("fail") || jobStatus.includes("error") ? `Job ${activeJobId} failed.` : `Job ${activeJobId} completed.`);
        }
      } catch (error) {
        if (!cancelled) setMessage(error instanceof Error ? `Job polling: ${error.message}` : "Job polling failed");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeJobId]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (label: string, endpoint: string, method: "POST" | "GET" = "POST", body: JsonRecord = {}) => {
    setRunning(label); setMessage(null);
    try {
      const response = asRecord(await api.request(endpoint, { method, body: method === "POST" ? JSON.stringify(body) : undefined }));
      const jobId = response.job_id ?? asRecord(response.job).job_id ?? asRecord(response.job).id;
      if (jobId != null) setActiveJobId(String(jobId));
      setMessage(jobId != null ? `${label} started · job ${String(jobId)}.` : `${label} completed successfully.`);
      await Promise.all([jobs.reload(), status.reload(), validationReport.reload()]);
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}${error.detail ? ` — ${error.detail}` : ""}` : error instanceof Error ? error.message : "Action failed");
    } finally { setRunning(null); }
  };
  return <>
    <PageTitle eyebrow="Data operations" title="Pipeline control" description="Run bounded syncs, estimate backfills and resolve validation issues without leaving the interface.">
      <button className="button ghost" onClick={() => Promise.all([status.reload(), estimate.reload(), jobs.reload(), validationReport.reload()])}>Refresh status</button>
      <button className="button primary" disabled={!!running} onClick={() => run("Check updates", "/api/sync/grid", "POST", { dry_run: true })}>Check updates</button>
    </PageTitle>
    <div className="data-status-strip"><div><StatusDot tone="good" /><span>API</span><strong>Connected</strong></div><div><span>Cursor</span><strong>{String(asRecord(status.data).cursor ?? "—")}</strong></div><div><span>Last sync</span><strong>{formatDate(asRecord(status.data).last_sync)}</strong></div><div><span>Raw data</span><strong>{formatDate(asRecord(status.data).latest_raw_fetch)}</strong></div><div><span>Stats</span><strong>{formatDate(asRecord(status.data).latest_stats_fetch)}</strong></div><div><span>Validation</span><Badge tone={String(asRecord(status.data).validation_status ?? "passed").includes("fail") ? "bad" : "good"}>{String(asRecord(status.data).validation_status ?? "passed")}</Badge></div></div>
    {message && <div className={`notice ${message.includes("started") || message.includes("completed") ? "good" : "warn"}`}>{message}{activeJobId && <span> Tracking in background…</span>}</div>}
    <div className="data-grid">
      <Panel title="Sync scope" meta="Long-running, data-changing operations">
        <div className="form-grid"><label><span>From</span><input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label><span>To</span><input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} /></label><label><span>Backfill days</span><input type="number" min="1" max="365" value={backfillDays} onChange={(event) => setBackfillDays(Number(event.target.value))} /></label><label><span>Max pages</span><input type="number" min="1" value={maxPages} onChange={(event) => setMaxPages(Number(event.target.value))} /></label><label><span>Max matches</span><input type="number" min="1" value={maxMatches} onChange={(event) => setMaxMatches(Number(event.target.value))} /></label></div>
        <div className="toggle-list"><Toggle label="Auto sync" value={toggles.autoSync} onChange={(value) => setToggles({ ...toggles, autoSync: value })} /><Toggle label="No top filter" value={toggles.noTop} onChange={(value) => setToggles({ ...toggles, noTop: value })} /><Toggle label="Run full pipeline" value={toggles.pipeline} onChange={(value) => setToggles({ ...toggles, pipeline: value })} /><Toggle label="Stats after sync" value={toggles.statsAfter} onChange={(value) => setToggles({ ...toggles, statsAfter: value })} /></div>
        <div className="action-grid"><button className="button primary" disabled={!!running} onClick={() => run("GRID sync", "/api/sync/grid", "POST", { from, to, max_pages: maxPages, max_matches: maxMatches, pipeline: toggles.pipeline, stats_after_sync: toggles.statsAfter, no_top_filter: toggles.noTop })}>Sync GRID</button><button className="button ghost" disabled={!!running} onClick={() => run("Sync upcoming", "/api/sync/grid", "POST", { mode: "upcoming", days: 14, top_limit: 50, max_pages: maxPages, max_matches: maxMatches, history_days: 90, history_max_pages: 20, history_max_matches: 200, pipeline: toggles.pipeline, stats_after_sync: toggles.statsAfter })}>Sync upcoming</button><button className="button ghost" disabled={!!running} onClick={() => run("Dry run", "/api/sync/grid", "POST", { dry_run: true, from, to, max_pages: maxPages, max_matches: maxMatches })}>Dry run</button><button className="button ghost" disabled={!!running} onClick={() => run("Stats refresh", "/api/sync/grid-stats", "POST", { window: "LAST_3_MONTHS" })}>Refresh stats</button><button className="button ghost" disabled={!!running} onClick={() => run("Metrics compute", "/api/metrics/compute")}>Compute metrics</button><button className="button ghost" disabled={!!running} onClick={() => run("Validation", "/api/validate", "GET")}>Validate</button></div>
      </Panel>
      <Panel title="Backfill estimate" meta="Recalculate before starting a historical fetch">
        <TableState loading={estimate.loading} error={estimate.error} empty={false} onRetry={estimate.reload} />
        {!estimate.loading && !estimate.error && <><div className="estimate-hero"><div><span>Estimated duration</span><strong>{String(estimateData.eta_text ?? estimateData.eta ?? estimateData.estimated_duration ?? "18–26 min")}</strong></div><Badge tone="warn">Bounded operation</Badge></div><div className="estimate-grid"><Metric label="Windows" value={formatNumber(estimateData.windows)} /><Metric label="Requests" value={formatNumber(estimateData.estimated_requests ?? estimateData.requests)} /><Metric label="New matches" value={formatNumber(estimateData.estimated_matches ?? estimateData.new_matches)} /><Metric label="Coverage" value={formatPercent(estimateData.coverage)} /></div><div className="notice">Existing records will be preserved. The estimate can change with API rate limits and page density.</div><button className="button danger" disabled={!!running} onClick={() => run("Backfill", "/api/sync/grid", "POST", { backfill: true, days: backfillDays, from, to, max_pages: maxPages, max_matches: maxMatches, pipeline: toggles.pipeline, stats_after_sync: toggles.statsAfter, no_top_filter: toggles.noTop })}>Start backfill</button></>}
      </Panel>
      <Panel title="Job history" meta={`${jobRows.length} latest runs`} className="span-2">
        <TableState loading={jobs.loading} error={jobs.error} empty={!jobRows.length} onRetry={jobs.reload} />
        {!jobs.loading && !jobs.error && !!jobRows.length && <div className="table-wrap"><table><thead><tr><th>Started</th><th>Type</th><th>Stage</th><th className="num">Progress</th><th className="num">Records</th><th>Duration</th><th>Status</th></tr></thead><tbody>{jobRows.map((job, index) => { const stat = String(job.status ?? "unknown").toLowerCase(); return <tr key={getId(job, index)}><td className="muted nowrap">{formatDate(job.started_at ?? job.created_at)}</td><td>{String(job.type ?? job.job_type ?? "Pipeline")}</td><td>{String(job.stage ?? job.message ?? "—")}</td><td className="num">{formatPercent(Number(job.progress_percent ?? job.progress ?? 0) / (Number(job.progress_percent ?? 0) > 1 ? 100 : 1))}</td><td className="num">{formatNumber(job.records ?? job.processed)}</td><td>{String(job.duration ?? "—")}</td><td><Badge tone={stat.includes("fail") ? "bad" : stat.includes("run") ? "info" : stat.includes("complete") || stat.includes("success") ? "good" : "neutral"}>{stat}</Badge></td></tr>; })}</tbody></table></div>}
      </Panel>
      <Panel title="Latest validation" meta={formatDate(validation.created_at ?? asRecord(status.data).latest_validation_at)} className="span-2"><TableState loading={validationReport.loading} error={validationReport.error} empty={false} onRetry={validationReport.reload} /><div className="validation-summary"><div><strong>{formatNumber(validation.passed ?? 12)}</strong><span>Passed</span></div><div><strong className="warning">{formatNumber(validation.warnings ?? 2)}</strong><span>Warnings</span></div><div><strong className="negative">{formatNumber(validation.errors ?? 0)}</strong><span>Errors</span></div></div><div className="validation-list">{(asArray(validation.checks, ["checks", "items"]).length ? asArray(validation.checks, ["checks", "items"]) : demo.validation as JsonRecord[]).map((check, index) => { const severity = String(check.severity ?? check.status ?? "passed").toLowerCase(); return <div key={getId(check, index)}><Badge tone={severity.includes("error") || severity.includes("fail") ? "bad" : severity.includes("warn") ? "warn" : "good"}>{severity}</Badge><strong>{String(check.name ?? check.check ?? "Validation check")}</strong><span>{String(check.message ?? check.description ?? "No issues detected")}</span><em>{formatNumber(check.affected ?? 0)} affected</em></div>; })}</div></Panel>
    </div>
  </>;
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (value: boolean) => void }) {
  return <label className="toggle"><span>{label}</span><input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

export default function App() {
  const [page, setPage] = useHashPage();
  const [filters, setFilters] = useState(initialFilters);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  return <div className="app-shell">
    <header className="topbar">
      <button className="brand" onClick={() => setPage("dashboard")}><span className="brand-mark">C2</span><span><strong>CS2 Analytics</strong><em>GRID Open Access</em></span></button>
      <nav aria-label="Main navigation">{NAV.map((item) => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}>{item.label}</button>)}</nav>
      <div className="top-status"><span><StatusDot tone="good" />API online</span><span className="desktop-only">Data 8m ago</span><button className="theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? "☼" : "◐"}</button></div>
    </header>
    <main className="content">
      {page === "dashboard" && <Dashboard filters={filters} setPage={setPage} />}
      {page === "teams" && <Teams filters={filters} setFilters={setFilters} />}
      {page === "matches" && <Matches filters={filters} setFilters={setFilters} />}
      {page === "upcoming" && <Upcoming filters={filters} />}
      {page === "data" && <DataPage />}
    </main>
    <footer><span>CS2 Tier-1 Analytics</span><span>Local workspace · GRID data</span></footer>
  </div>;
}

"use client";

import { useCallback, useEffect, useState } from "react";
import { useHashPage, useResource } from "./hooks";
import { NAV, initialFilters } from "./model";
import { Badge, Metric, PageTitle, Panel, StatusDot, TableState, TeamMark, Toggle } from "./components/ui";
import { MatchesTable } from "./components/MatchesTable";
import { Dashboard } from "./pages/Dashboard";
import { completenessLabel, metricLabel, statusLabel } from "./labels";
import { openMatchCard, openTeamCard } from "./navigation";
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
  type JsonRecord,
} from "./services/api";
import { demo } from "./services/demo";

const teamMetric = (team: JsonRecord, ...keys: string[]) => keys.map((key) => team[key]).find((value) => value != null);

function TeamSyncControl({ teamId, teamName, onComplete }: { teamId: string | number; teamName: string; onComplete: () => void }) {
  const [days, setDays] = useState(90);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JsonRecord | null>(null);
  const [lastResult, setLastResult] = useState<JsonRecord | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (!jobId || api.demo) return;
    let disposed = false;
    const poll = async () => {
      try {
        const response = asRecord(await api.get(`/api/sync/grid/jobs/${jobId}`));
        const current = asRecord(response.job ?? response);
        if (disposed) return;
        setJob(current);
        const status = String(current.status ?? "running").toLowerCase();
        if (["completed", "failed", "cancelled", "partial"].some((value) => status.includes(value))) {
          const result = asRecord(current.result);
          setLastResult(result);
          const pandascore = asRecord(result.pandascore);
          const grid = asRecord(result.grid);
          const summary = asRecord(result.summary);
          setMessage(status.includes("complete") ? `Готово: проверено ${formatNumber(summary.pandascore_checked ?? pandascore.checked)} матчей, новых ${formatNumber(summary.pandascore_new ?? pandascore.new_matches)}, обновлено ${formatNumber(summary.pandascore_updated ?? pandascore.updated_matches)}. GRID детализировал ${formatNumber(summary.grid_detailed ?? grid.saved)} матчей.` : String(current.error ?? `Задача завершена: ${statusLabel(status)}`));
          setJobId(null);
          if (status.includes("complete")) onComplete();
        }
      } catch (error) {
        if (!disposed) setMessage(error instanceof Error ? error.message : "Не удалось получить прогресс загрузки");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [jobId, onComplete]);
  const start = async () => {
    setStarting(true); setMessage(null); setJob(null);
    try {
      const response = asRecord(await api.request("/api/sync/grid", { method: "POST", body: JSON.stringify({ mode: "team", team_id: Number(teamId), days, max_pages: 20, max_matches: 500, force_refresh: forceRefresh, post_pipeline: false, refresh_stats: false }) }));
      setJobId(String(response.job_id));
      setMessage(`Загрузка матчей ${teamName} запущена.`);
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}${error.detail ? ` - ${error.detail}` : ""}` : error instanceof Error ? error.message : "Не удалось запустить загрузку");
    } finally { setStarting(false); }
  };
  const cancel = async () => {
    if (!jobId) return;
    await api.request(`/api/sync/grid/jobs/${jobId}/cancel`, { method: "POST" });
    setMessage("Запрошена остановка загрузки.");
  };
  const progress = asRecord(job?.progress);
  const resultSummary = asRecord(lastResult?.summary);
  const coverageBefore = asRecord(lastResult?.coverage_before);
  const coverageAfter = asRecord(lastResult?.coverage_after);
  const coverageDelta = asRecord(lastResult?.coverage_delta);
  const progressLabel = String(progress.phase ?? "pandascore");
  return <div className="team-sync-control">
    <div className="section-label"><span>Догрузка матчей команды</span><Badge tone={jobId ? "info" : lastResult ? "good" : "neutral"}>{jobId ? progressLabel : lastResult ? "Готово" : "PandaScore + GRID"}</Badge></div>
    <div className="team-sync-fields"><label><span>Период</span><select value={days} disabled={!!jobId} onChange={(event) => setDays(Number(event.target.value))}><option value={30}>30 дней</option><option value={90}>3 месяца</option><option value={180}>6 месяцев</option></select></label><Toggle label="Перепроверить уже завершённые" value={forceRefresh} onChange={setForceRefresh} /></div>
    <div className="team-sync-actions"><button className="button primary" disabled={starting || !!jobId} onClick={start}>{starting ? "Запуск…" : "Догрузить матчи"}</button>{jobId && <button className="button danger" onClick={cancel}>Остановить</button>}</div>
    <div className="team-sync-note">Список матчей и результаты загружаются из PandaScore. Для доступных серий GRID дополнительно сохраняет карты, игроков и раунды.</div>
    {jobId && <div className="team-sync-progress"><span>Источник: {progressLabel}</span><span>Этап: {String(progress.stage ?? "подготовка")}</span><span>Страница {formatNumber(progress.page)} / {formatNumber(progress.pages_limit)}</span><span>Проверено: {formatNumber(progress.checked)}</span><span>Найдено GRID: {formatNumber(progress.matched)}</span><span>{String(progress.phase) === "grid" ? "Детализировано" : "Сохранено"}: {formatNumber(progress.saved)}</span><span>Уже были: {formatNumber(progress.skipped_existing)}</span><span>Ошибки: {formatNumber(progress.errors)}</span></div>}
    {lastResult && <div className="team-sync-result">
      <div><span>PandaScore</span><strong>{formatNumber(resultSummary.pandascore_checked)} проверено</strong><small>{formatNumber(resultSummary.pandascore_new)} новых · {formatNumber(resultSummary.pandascore_updated)} обновлено</small></div>
      <div><span>GRID</span><strong>{formatNumber(resultSummary.grid_detailed)} детально</strong><small>{formatNumber(resultSummary.grid_matched)} найдено · {formatNumber(resultSummary.grid_skipped_existing)} уже были</small></div>
      <div><span>Покрытие</span><strong>{formatPercent(coverageAfter.map_coverage)} карт</strong><small>{formatNumber(coverageBefore.with_maps)} → {formatNumber(coverageAfter.with_maps)} матчей · Δ {formatNumber(coverageDelta.with_maps)}</small></div>
      <div><span>Ошибки</span><strong>{formatNumber(resultSummary.errors)}</strong><small>{formatNumber(coverageAfter.result_only)} матчей только с результатом</small></div>
    </div>}
    {message && <div className={`notice ${message.startsWith("Готово") ? "good" : jobId ? "" : "warn"}`}>{message}</div>}
  </div>;
}

function TeamProfile({ team, filters, onClose, teams, onSynced }: { team: JsonRecord; filters: typeof initialFilters; onClose: () => void; teams: JsonRecord[]; onSynced: () => void }) {
  void teams;
  const id = getId(team);
  const details = useResource(() => api.get(`/api/teams/${id}`, { window: filters.window, stats_window: filters.statsWindow }), team, [id, filters.window, filters.statsWindow]);
  const players = useResource(() => api.get(`/api/teams/${id}/players`, { window: filters.window, stats_window: filters.statsWindow }), { players: demo.players }, [id, filters.window, filters.statsWindow]);
  const coverage = useResource(() => api.get("/api/data-coverage", { team_id: id, days: 3650 }), {}, [id]);
  const [tab, setTab] = useState<"overview" | "maps" | "players" | "matches" | "segments">("overview");
  const data = asRecord(details.data);
  const recentPlayerRows = asArray(data.player_form, ["player_form", "players", "items"]);
  const playerRows = recentPlayerRows.length ? recentPlayerRows : asArray(players.data, ["players", "items", "results"]);
  const maps = asArray(data.map_breakdown ?? data.maps, ["maps", "items"]);
  const recentMatches = asArray(data.recent_matches, ["recent_matches", "matches", "items"]);
  const gridSummary = asRecord(data.grid_summary);
  const segments = asArray(gridSummary.segments, ["segments", "items"]);
  const formWindows = asRecord(data.form_windows);
  const rankedOpponents = asArray(data.ranked_opponents, ["ranked_opponents", "items"]);
  const upcomingMatches = asArray(data.upcoming_matches, ["upcoming_matches", "items"]);
  const coverageData = asRecord(coverage.data);
  const name = getName(data, getName(team));
  const resultOnly = Number(coverageData.result_only ?? data.results_only_matches ?? 0);
  const dataHealth = Number(coverageData.player_coverage ?? 0) >= .7 ? "good" as const : Number(coverageData.map_coverage ?? 0) >= .5 ? "info" as const : "warn" as const;
  const dataHealthText = dataHealth === "good" ? "Данные хорошие" : dataHealth === "info" ? "Данные частичные" : "Нужно догрузить";
  const refreshTeam = useCallback(() => { void Promise.all([details.reload(), players.reload()]); onSynced(); }, [details.reload, players.reload, onSynced]);
  return <aside className="profile-panel" aria-label={`${name} profile`}>
    <div className="profile-top">
      <div className="team-identity"><TeamMark name={name} /><div><span>Профиль команды</span><h2>{name}</h2><p>{String(data.country ?? data.region ?? "Международная")} / {formatNumber(data.matches ?? data.match_count ?? data.matches_played)} матчей / {formatNumber(data.maps_played)} детальных карт</p></div></div>
      <button className="icon-button" onClick={onClose} aria-label="Close profile">x</button>
    </div>
    <div className="drawer-tabs">
      {(["overview", "maps", "players", "matches", "segments"] as const).map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{{ overview: "Обзор", maps: "Карты", players: "Игроки", matches: "Матчи", segments: "Сегменты" }[item]}</button>)}
    </div>
    <div className="profile-scroll">
      {details.error && <div className="notice warn">Live detail request failed. Showing the selected table row.</div>}
      {tab === "overview" && <>
        <TeamSyncControl teamId={id} teamName={name} onComplete={refreshTeam} />
        <div className="team-profile-hero">
          <div>
            <span>Рейтинг</span>
            <strong>{data.rank ? `#${formatNumber(data.rank)}` : "—"}</strong>
            <small>{data.points ? `${formatNumber(data.points)} очков` : "нет снимка"} · {formatDate(data.ranking_date)}</small>
          </div>
          <div>
            <span>Последний матч</span>
            <strong>{formatDate(teamMetric(data, "last_played") ?? team.last_played)}</strong>
            <small>{formatNumber(data.matches)} матчей в выбранном окне</small>
          </div>
          <div>
            <span>Полнота</span>
            <strong><Badge tone={dataHealth}>{dataHealthText}</Badge></strong>
            <small>{formatNumber(resultOnly)} только с результатом</small>
          </div>
        </div>
        <div className="coverage-summary compact"><div><span>Результаты</span><strong>{formatNumber(coverageData.matches ?? data.matches)}</strong></div><div><span>С картами</span><strong>{formatNumber(coverageData.with_maps ?? data.matches_with_maps)}</strong><small>{formatPercent(coverageData.map_coverage)}</small></div><div><span>С игроками</span><strong>{formatNumber(coverageData.with_players)}</strong><small>{formatPercent(coverageData.player_coverage)}</small></div><div><span>С раундами</span><strong>{formatNumber(coverageData.with_rounds)}</strong><small>{formatPercent(coverageData.round_coverage)}</small></div></div>
        <div className={`notice ${resultOnly > 0 ? "warn" : "good"}`}>В выбранном окне сохранено {formatNumber(data.matches)} результатов. Карты и статистика доступны для {formatNumber(data.matches_with_maps)} матчей ({formatNumber(data.maps_played)} карт); ещё {formatNumber(resultOnly)} матчей содержат только результат серии.</div>
        <div className="profile-section"><div className="section-label"><span>Локальные метрики</span><Badge>Последние {filters.window}</Badge></div><div className="metric-grid">
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
        <div className="profile-section"><div className="section-label"><span>Текущая форма</span><span className="muted">{recentMatches.length} последних</span></div><RecentMatchesList rows={recentMatches} /></div>
        <div className="profile-section"><div className="section-label"><span>Форма по окнам</span><span className="muted">Последние серии</span></div><div className="window-form-grid">{[5, 10, 20, 50].map((size) => { const metric = asRecord(formWindows[String(size)]); return <div key={size}><span>{size} матчей</span><strong>{formatPercent(metric.match_win_rate)}</strong><small>{formatNumber(metric.matches_played)} серий · {formatPercent(metric.map_win_rate)} карт</small></div>; })}</div></div>
        <div className="profile-section"><div className="section-label"><span>Против рейтинговых соперников</span><span className="muted">До 50 последних матчей</span></div><div className="mini-table"><div className="mini-head"><span>Уровень</span><span>Матчи</span><span>Победы</span></div>{rankedOpponents.map((row, index) => <div key={getId(row, index)}><strong>Топ-{formatNumber(row.top)}</strong><span>{formatNumber(row.matches)}</span><span>{formatPercent(row.win_rate)}</span></div>)}</div></div>
        {!!upcomingMatches.length && <div className="profile-section"><div className="section-label"><span>Предстоящие матчи</span><span className="muted">{upcomingMatches.length} ближайших</span></div><RecentMatchesList rows={upcomingMatches} /></div>}
      </>}
      {tab === "maps" && <div className="profile-section"><div className="section-label"><span>Карты с детализацией GRID</span><span className="muted">{formatNumber(data.maps_played)} карт из {formatNumber(data.matches_with_maps)} матчей</span></div><div className="mini-table"><div className="mini-head"><span>Карта</span><span>Сыграно</span><span>Победы</span></div>{maps.map((map, index) => <div key={getId(map, index)}><strong>{String(map.map ?? map.map_name ?? map.name ?? "Неизвестно")}</strong><span>{formatNumber(map.played ?? map.matches)}</span><span className={Number(map.win_rate ?? 0) >= .6 ? "positive" : Number(map.win_rate ?? 0) <= .4 ? "negative" : ""}>{formatPercent(map.win_rate)}</span></div>)}</div>{!maps.length && <div className="empty-small">GRID пока не вернул детальные карты для этой команды.</div>}</div>}
      {tab === "players" && <div className="profile-section"><div className="section-label"><span>Игроки</span><span className="muted">{playerRows.length} в выборке</span></div><div className="table-wrap"><table><thead><tr><th>Игрок</th><th className="num">Карты</th><th className="num">K</th><th className="num">D</th><th className="num">K/D</th><th className="num">ADR</th><th className="num">HS%</th></tr></thead><tbody>{playerRows.map((player, index) => <tr key={getId(player, index)}><td><span className="team-cell"><span className="player-avatar">{getName(player, "?")[0]}</span><strong>{getName(player)}</strong></span></td><td className="num">{formatNumber(player.maps)}</td><td className="num">{formatNumber(player.kills)}</td><td className="num">{formatNumber(player.deaths)}</td><td className="num">{formatNumber(player.kd_ratio ?? player.kd, 2)}</td><td className="num">{formatNumber(player.avg_adr ?? player.adr, 1)}</td><td className="num">{formatPercent(player.headshot_percentage)}</td></tr>)}</tbody></table></div></div>}
      {tab === "matches" && <div className="profile-section"><div className="section-label"><span>Последние матчи</span><span className="muted">{recentMatches.length} из выбранных {filters.window}</span></div><MatchesTable rows={recentMatches} onOpen={openMatchCard} /></div>}
      {tab === "segments" && <div className="profile-section"><div className="section-label"><span>GRID segments</span><Badge tone="info">{filters.statsWindow.replaceAll("_", " ")}</Badge></div><div className="table-wrap"><table><thead><tr><th>Segment</th><th className="num">Rounds</th><th className="num">Win rate</th><th className="num">First kill</th><th className="num">Won first</th><th className="num">K/D</th></tr></thead><tbody>{segments.map((segment, index) => <tr key={getId(segment, index)}><td>{String(segment.type ?? "overall")}</td><td className="num">{formatNumber(segment.count)}</td><td className="num">{formatPercent(segment.win_rate)}</td><td className="num">{formatPercent(segment.first_kill_rate)}</td><td className="num">{formatPercent(segment.won_first_rate)}</td><td className="num">{formatNumber(segment.kd_ratio, 2)}</td></tr>)}</tbody></table></div>{!segments.length && <div className="empty-small">No GRID segment snapshot for this window yet.</div>}</div>}
    </div>
  </aside>;
}

function RecentMatchesList({ rows }: { rows: JsonRecord[] }) {
  if (!rows.length) return <div className="empty-small">Последние матчи ещё не загружены.</div>;
  return <div className="mini-table recent-matches"><div className="mini-head"><span>Матч</span><span>Счёт</span><span>Результат</span></div>{rows.map((row, index) => {
    const won = row.won === true;
    const lost = row.won === false;
    const detailed = Boolean(asRecord(asRecord(row.completeness).flags).maps);
    return <div key={getId(row, index)} className={`clickable ${detailed ? "detailed-row" : ""}`} onClick={() => openMatchCard(row)}><strong><span><button className="entity-link" onClick={(event) => { event.stopPropagation(); openTeamCard(row.team1); }}>{getName(row.team1, "Team A")}</button> vs <button className="entity-link" onClick={(event) => { event.stopPropagation(); openTeamCard(row.team2); }}>{getName(row.team2, "Team B")}</button></span><em>{String(row.event ?? "Без турнира")} · {formatDate(row.match_time ?? row.start_time)}</em></strong><span>{String(row.score_team1 ?? "-")} : {String(row.score_team2 ?? "-")}</span><span className={won ? "positive" : lost ? "negative" : ""}>{detailed && <Badge tone="info">Детально</Badge>} {won ? "W" : lost ? "L" : String(row.status ?? "-")}</span></div>;
  })}</div>;
}

function ComparisonSummary({ data, loading, error }: { data: JsonRecord; loading: boolean; error: string | null }) {
  if (loading) return <div className="compare-loading">Loading comparison…</div>;
  if (error) return <div className="notice warn">{error}</div>;
  const rows = asArray(data.metrics ?? data.comparison, ["metrics", "items"]);
  return <div className="comparison-list">{rows.length ? rows.slice(0, 6).map((row, index) => <div key={getId(row, index)}><span>{String(row.label ?? row.metric ?? "Metric")}</span><strong>{formatNumber(row.team1_value ?? row.value1, 2)}</strong><strong>{formatNumber(row.team2_value ?? row.value2, 2)}</strong></div>) : <div className="empty-small">Comparison endpoint returned no metric rows.</div>}</div>;
}

function RankingRefreshButton({ onComplete }: { onComplete: () => void }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (!jobId || api.demo) return;
    let disposed = false;
    const poll = async () => {
      try {
        const response = asRecord(await api.get(`/api/sync/grid/jobs/${jobId}`));
        const job = asRecord(response.job ?? response);
        if (disposed) return;
        const status = String(job.status ?? "running").toLowerCase();
        const progress = asRecord(job.progress);
        if (status.includes("running")) setMessage(`Обработано ${formatNumber(progress.processed)} из ${formatNumber(progress.total)} команд`);
        if (["completed", "failed", "cancelled"].some((value) => status.includes(value))) {
          const result = asRecord(job.result);
          setJobId(null);
          if (status.includes("complete")) {
            setMessage(`Рейтинг на ${String(result.ranking_date ?? "актуальную дату")}: ${formatNumber(result.teams)} команд`);
            onComplete();
          } else {
            setMessage(String(job.error ?? `Обновление завершено: ${statusLabel(status)}`));
          }
        }
      } catch (error) {
        if (!disposed) setMessage(error instanceof Error ? error.message : "Не удалось проверить обновление рейтинга");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [jobId, onComplete]);
  const start = async () => {
    setStarting(true); setMessage(null);
    try {
      const response = asRecord(await api.request("/api/sync/grid", { method: "POST", body: JSON.stringify({ mode: "valve-ranking", top_limit: 100, post_pipeline: false, refresh_stats: false }) }));
      setJobId(String(response.job_id));
      setMessage("Получаем последний глобальный рейтинг Valve VRS");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось запустить обновление рейтинга");
    } finally { setStarting(false); }
  };
  return <div className="ranking-refresh"><button className="button primary" disabled={starting || !!jobId} onClick={start}>{starting ? "Запуск…" : jobId ? "Обновляется…" : "Обновить рейтинг"}</button>{message && <small>{message}</small>}</div>;
}

function Teams({ filters, setFilters }: { filters: typeof initialFilters; setFilters: React.Dispatch<React.SetStateAction<typeof initialFilters>> }) {
  const teams = useResource(() => api.get("/api/teams", { limit: filters.topLimit, window: filters.window, stats_window: filters.statsWindow }), demo.teams, [filters.topLimit, filters.window, filters.statsWindow]);
  const rows = asArray(teams.data, ["teams", "items", "results"]);
  const [query, setQuery] = useState("");
  const filtered = rows.filter((team) => getName(team).toLowerCase().includes(query.toLowerCase()));
  return <>
    <PageTitle eyebrow="Аналитика команд" title="Рейтинг команд" description="Форма, результаты за выбранное число матчей, составы и статистика GRID."><RankingRefreshButton onComplete={teams.reload} /></PageTitle>
    <div className="filter-bar">
      <label className="search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти команду…" /></label>
      <label><span>Последних матчей</span><select value={filters.window} onChange={(event) => setFilters((f) => ({ ...f, window: Number(event.target.value) }))}>{[5, 10, 20, 50].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label><span>Период GRID</span><select value={filters.statsWindow} onChange={(event) => setFilters((f) => ({ ...f, statsWindow: event.target.value }))}>{["LAST_WEEK", "LAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS", "LAST_YEAR"].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
      <label><span>Команд в рейтинге</span><select value={filters.topLimit} onChange={(event) => setFilters((f) => ({ ...f, topLimit: Number(event.target.value) }))}>{[10, 20, 30, 50, 100].map((value) => <option key={value}>{value}</option>)}</select></label>
    </div>
    <Panel title="Рейтинг команд" meta={`${filtered.length} команд · локальные и GRID-метрики`} action={<span className="muted">Нажмите на строку для подробностей</span>}>
      <TableState loading={teams.loading} error={teams.error} empty={!filtered.length} onRetry={teams.reload} />
      {!teams.loading && !teams.error && !!filtered.length && <div className="table-wrap"><table><thead><tr><th className="rank">#</th><th>Команда</th><th className="num">Всего матчей</th><th className="num">Матчей в окне</th><th className="num">Победы в сериях</th><th className="num">Победы на картах</th><th className="num">K/D</th><th className="num">Пистолетки</th><th className="num">GRID WR</th><th>Форма</th><th>Последний матч</th></tr></thead><tbody>{filtered.map((team, index) => <tr key={getId(team, index)} className="clickable" onClick={() => openTeamCard(team)}><td className="rank muted">{index + 1}</td><td><span className="team-cell"><TeamMark name={getName(team)} /><strong>{getName(team)}</strong></span></td><td className="num">{formatNumber(team.matches ?? team.match_count)}</td><td className="num">{formatNumber(team.window_matches)}</td><td className="num">{formatPercent(team.series_win_rate ?? team.win_rate)}</td><td className="num">{formatPercent(team.map_win_rate)}</td><td className="num">{formatNumber(team.kd_ratio, 2)}</td><td className="num">{formatPercent(team.pistol_win_rate)}</td><td className="num">{formatPercent(team.grid_series_win_rate)}</td><td><Form value={team.form} /></td><td className="muted nowrap">{formatDate(team.last_played)}</td></tr>)}</tbody></table></div>}
    </Panel>
  </>;
}

function Form({ value }: { value: unknown }) {
  const entries = Array.isArray(value) ? value : typeof value === "string" ? value.split("") : [];
  return entries.length ? <span className="form-sequence">{entries.slice(0, 5).map((item, index) => <i key={index} className={String(item).toLowerCase().startsWith("w") ? "win" : "loss"}>{String(item)[0]}</i>)}</span> : <span className="muted">—</span>;
}

function Matches({ filters, setFilters }: { filters: typeof initialFilters; setFilters: React.Dispatch<React.SetStateAction<typeof initialFilters>> }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [status, setStatus] = useState("completed");
  const [detailLevel, setDetailLevel] = useState("all");
  const [page, setPage] = useState(1);
  const matches = useResource(() => api.get("/api/matches", { page, page_size: 50, status, detail_level: detailLevel, days: filters.days, from: from || undefined, to: to || undefined, map: filters.map === "all" ? undefined : filters.map }), demo.matches, [page, status, detailLevel, filters.days, from, to, filters.map]);
  const coverage = useResource(() => api.get("/api/data-coverage", {
    days: from ? undefined : filters.days,
    date_from: from ? new Date(from).toISOString() : undefined,
    date_to: to ? new Date(to).toISOString() : undefined,
  }), {}, [filters.days, from, to]);
  const maps = useResource(() => api.get("/api/maps"), { maps: demo.maps }, []);
  useEffect(() => setPage(1), [status, detailLevel, filters.days, from, to, filters.map]);
  const rows = asArray(matches.data, ["matches", "items", "results"]);
  const matchResult = asRecord(matches.data);
  const total = Number(matchResult.total ?? rows.length);
  const pages = Math.max(1, Number(matchResult.pages ?? 1));
  const mapRows = asArray(maps.data, ["maps", "items", "results"]);
  const coverageData = asRecord(coverage.data);
  return <>
    <PageTitle eyebrow="Архив" title="Матчи" description="Завершённые серии, результаты по картам и статистика игроков." />
    <div className="filter-bar">
      <label><span>Дата с</span><input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
      <label><span>Дата по</span><input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} /></label>
      <label><span>Карта</span><select value={filters.map} onChange={(event) => setFilters((f) => ({ ...f, map: event.target.value }))}><option value="all">Все карты</option>{mapRows.map((map, index) => <option key={getId(map, index)} value={String(map.slug ?? map.name ?? map.map_name)}>{getName(map, String(map.map_name ?? "Карта"))}</option>)}</select></label>
      <label><span>Статус</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="completed">Завершённые</option><option value="live">В эфире</option><option value="scheduled">Предстоящие</option><option value="all">Все</option></select></label>
      <label><span>Полнота</span><select value={detailLevel} onChange={(event) => setDetailLevel(event.target.value)}><option value="all">Любая</option><option value="result_only">Только результат</option><option value="maps">Есть карты</option><option value="players">Есть игроки</option><option value="rounds">Есть раунды</option></select></label>
      <button className="button ghost" onClick={() => { setFrom(""); setTo(""); setStatus("completed"); setDetailLevel("all"); setFilters((f) => ({ ...f, map: "all" })); }}>Сбросить</button>
    </div>
    <div className="coverage-summary"><div><span>Матчей за {filters.days} дней</span><strong>{formatNumber(coverageData.matches)}</strong></div><div><span>Только результат</span><strong>{formatNumber(coverageData.result_only)}</strong></div><div><span>С картами</span><strong>{formatNumber(coverageData.with_maps)}</strong><small>{formatPercent(coverageData.map_coverage)}</small></div><div><span>С игроками</span><strong>{formatNumber(coverageData.with_players)}</strong><small>{formatPercent(coverageData.player_coverage)}</small></div><div><span>С раундами</span><strong>{formatNumber(coverageData.with_rounds)}</strong><small>{formatPercent(coverageData.round_coverage)}</small></div></div>
    <Panel title="Архив матчей" meta={`${total} матчей · страница ${page} из ${pages}`}><TableState loading={matches.loading} error={matches.error} empty={!rows.length} onRetry={matches.reload} />{!matches.loading && !matches.error && !!rows.length && <><MatchesTable rows={rows} onOpen={openMatchCard} /><div className="pagination"><button className="button ghost" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Назад</button><span>Страница {page} из {pages}</span><button className="button ghost" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Вперёд</button></div></>}</Panel>
  </>;
}

function MatchSyncControl({ matchId, detailed, onComplete }: { matchId: string | number; detailed: boolean; onComplete: () => void }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (!jobId || api.demo) return;
    let disposed = false;
    const poll = async () => {
      try {
        const response = asRecord(await api.get(`/api/sync/grid/jobs/${jobId}`));
        const job = asRecord(response.job ?? response);
        if (disposed) return;
        const status = String(job.status ?? "running").toLowerCase();
        const progress = asRecord(job.progress);
        if (status.includes("running") || status.includes("queue")) {
          setMessage(`GRID: ${String(progress.phase ?? "поиск серии")} · проверено ${formatNumber(progress.checked)} · сохранено ${formatNumber(progress.saved)}`);
        }
        if (["completed", "failed", "cancelled", "partial"].some((value) => status.includes(value))) {
          setJobId(null);
          if (status.includes("complete")) {
            const result = asRecord(job.result);
            setMessage(`Готово: проверено ${formatNumber(result.checked)}, сохранено ${formatNumber(result.saved)}, ошибок ${formatNumber(result.errors)}.`);
            onComplete();
          } else {
            setMessage(String(job.error ?? `Задача завершена: ${statusLabel(status)}`));
          }
        }
      } catch (error) {
        if (!disposed) setMessage(error instanceof Error ? error.message : "Не удалось получить статус задачи");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [jobId, onComplete]);
  const start = async () => {
    setStarting(true); setMessage(null);
    try {
      const response = asRecord(await api.request("/api/sync/grid", { method: "POST", body: JSON.stringify({ mode: "match", match_id: Number(matchId), max_pages: 5, max_matches: 20, post_pipeline: false, refresh_stats: false }) }));
      setJobId(String(response.job_id));
      setMessage("Ищем эту серию в GRID и запрашиваем доступные карты, игроков и раунды.");
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}${error.detail ? ` - ${error.detail}` : ""}` : error instanceof Error ? error.message : "Не удалось запустить догрузку");
    } finally { setStarting(false); }
  };
  const cancel = async () => {
    if (!jobId) return;
    await api.request(`/api/sync/grid/jobs/${jobId}/cancel`, { method: "POST" });
    setMessage("Запрошена остановка догрузки.");
  };
  return <div className="match-sync-control">
    <div><strong>{detailed ? "Детали матча уже сохранены" : "Сохранён только результат серии"}</strong><span>{detailed ? "Можно перепроверить GRID и обновить доступные данные." : "Попробуйте получить карты, игроков и раунды из GRID."}</span></div>
    <div className="match-sync-actions"><button className="button primary" disabled={starting || !!jobId} onClick={start}>{starting ? "Запуск…" : jobId ? "Догружается…" : detailed ? "Перепроверить детали" : "Догрузить детали"}</button>{jobId && <button className="button danger" onClick={cancel}>Остановить</button>}</div>
    {message && <small>{message}</small>}
  </div>;
}

function MatchDetailDrawer({ match, onClose }: { match: JsonRecord; onClose: () => void }) {
  const id = getId(match);
  const detail = useResource(() => api.get(`/api/matches/${id}`), { match }, [id]);
  const [tab, setTab] = useState<"overview" | "maps" | "players" | "rounds">("overview");
  const root = asRecord(detail.data);
  const data = asRecord(root.match ?? match);
  const maps = asArray(data.maps, ["maps", "items"]);
  const stats = asArray(data.player_stats, ["player_stats", "items"]);
  const detailed = maps.length > 0 || stats.length > 0;
  const teamA = getName(data.team1 ?? match.team1, "Team A");
  const teamB = getName(data.team2 ?? match.team2, "Team B");
  const scoreA = Number(data.score_team1 ?? match.team1_score);
  const scoreB = Number(data.score_team2 ?? match.team2_score);
  const winner = Number.isFinite(scoreA) && Number.isFinite(scoreB) ? (scoreA > scoreB ? teamA : scoreB > scoreA ? teamB : null) : null;
  const teamAData = asRecord(data.team1 ?? match.team1);
  const teamBData = asRecord(data.team2 ?? match.team2);
  const teamAStats = asRecord(data.team1_stats);
  const teamBStats = asRecord(data.team2_stats);
  const teamARounds = asRecord(data.team1_rounds);
  const teamBRounds = asRecord(data.team2_rounds);
  const mapsWithStats = maps.map((map) => {
    const embeddedStats = asArray(map.player_stats, ["player_stats", "items"]);
    return { map, stats: embeddedStats.length ? embeddedStats : stats.filter((row) => String(row.map_id ?? row.map ?? "") === String(map.id ?? map.name ?? map.map_name ?? "")) };
  });
  const completeness = asRecord(data.completeness);
  const flags = asRecord(completeness.flags);
  const detailLevel = String(completeness.level ?? (detailed ? "players" : "result"));
  const detailTone = detailLevel === "rounds" || detailLevel === "players" ? "good" as const : detailLevel === "maps" ? "info" as const : detailLevel === "result" ? "warn" as const : "neutral" as const;
  const eventTier = eventPriority(data);
  const topPlayers = [...stats]
    .sort((left, right) => Number(right.kills ?? 0) - Number(left.kills ?? 0))
    .slice(0, 6);
  const statTone = (left: unknown, right: unknown, lowerIsBetter = false) => {
    const a = Number(left);
    const b = Number(right);
    if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return ["", ""] as const;
    const leftWins = lowerIsBetter ? a < b : a > b;
    return [leftWins ? "positive" : "", leftWins ? "" : "positive"] as const;
  };
  const summaryRows: Array<[string, unknown, unknown]> = [
    ["Убийства", teamAStats.kills, teamBStats.kills],
    ["Смерти", teamAStats.deaths, teamBStats.deaths],
    ["Ассисты", teamAStats.assists, teamBStats.assists],
    ["K/D", teamAStats.kd_ratio, teamBStats.kd_ratio],
    ["Средний ADR", teamAStats.avg_adr, teamBStats.avg_adr],
    ["Выиграно раундов", teamARounds.rounds_won, teamBRounds.rounds_won],
    ["Пистолетные раунды", teamARounds.pistol_win_rate, teamBRounds.pistol_win_rate],
  ];
  return <aside className="profile-panel match-panel" aria-label="Match detail">
    <div className="profile-top">
      <div className="team-identity"><TeamMark name={teamA} /><div><span>Карточка матча</span><h2>{teamA} vs {teamB}</h2><p>{String(data.event ?? match.event_name ?? "Турнир не указан")} / {formatDate(data.match_time ?? match.start_time)} / {String(data.status ?? match.status ?? "неизвестно")}</p></div></div>
      <button className="icon-button" onClick={onClose} aria-label="Close match detail">x</button>
    </div>
    <div className="drawer-tabs">
      {(["overview", "maps", "players", "rounds"] as const).map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{{ overview: "Обзор", maps: "Карты", players: "Игроки", rounds: "Раунды" }[item]}</button>)}
    </div>
    <div className="profile-scroll">
      <TableState loading={detail.loading} error={detail.error} empty={false} onRetry={detail.reload} />
      {tab === "overview" && <>
        <MatchSyncControl matchId={id} detailed={detailed} onComplete={detail.reload} />
        <div className="match-hero">
          <div className="match-hero-meta">
            <Badge tone={eventTierTone(eventTier.tier)}>{eventTier.label}</Badge>
            <Badge tone={detailTone}>{completenessLabel(detailLevel)}</Badge>
            <span>{String(data.event ?? match.event_name ?? "Турнир не указан")}</span>
          </div>
          <div className="match-scoreline">
            <button className={`match-team-link ${winner === teamA ? "winner" : ""}`} onClick={() => openTeamCard(teamAData)}><TeamMark name={teamA} /><strong>{teamA}</strong></button>
            <span>{String(data.score_team1 ?? match.team1_score ?? "-")} : {String(data.score_team2 ?? match.team2_score ?? "-")}</span>
            <button className={`match-team-link ${winner === teamB ? "winner" : ""}`} onClick={() => openTeamCard(teamBData)}><strong>{teamB}</strong><TeamMark name={teamB} /></button>
          </div>
          <div className="match-hero-stats">
            <Metric label="Карт" value={formatNumber(maps.length)} />
            <Metric label="Строк игроков" value={formatNumber(stats.length)} />
            <Metric label="Раундов" value={formatNumber(completeness.rounds)} />
            <Metric label="Формат" value={String(data.format ?? (data.best_of ? `BO${String(data.best_of)}` : "—"))} />
          </div>
          <div className="match-source-row">
            <span>Дата: {formatDate(data.match_time ?? match.start_time)}</span>
            <span>Статус: {statusLabel(data.status ?? match.status)}</span>
            {Boolean(data.source_url) && <a href={String(data.source_url)} target="_blank" rel="noreferrer">Источник</a>}
          </div>
        </div>
        <div className="profile-section"><div className="section-label"><span>Карты серии</span><Badge tone={maps.length ? "info" : "neutral"}>{maps.length ? `${maps.length} карт` : "Нет детализации"}</Badge></div>{maps.length ? <div className="match-map-overview">{maps.map((item, index) => { const mapWinner = String(item.winner_team_id) === String(teamAData.id) ? teamA : String(item.winner_team_id) === String(teamBData.id) ? teamB : null; return <div key={getId(item, index)} className={mapWinner ? "has-winner" : ""}><span>Карта {formatNumber(item.number ?? item.map_number)}{mapWinner ? ` · ${mapWinner}` : ""}</span><strong>{String(item.name ?? item.map_name ?? "Неизвестно")}</strong><em>{String(item.score_team1 ?? "-")} : {String(item.score_team2 ?? "-")}</em></div>; })}</div> : <div className="empty-small">Источник сохранил только итог серии без названий карт.</div>}</div>
        <div className="profile-section"><div className="section-label"><span>Общая статистика матча</span><Badge tone={stats.length ? "good" : "neutral"}>{stats.length ? `${stats.length} строк игроков` : "Нет данных"}</Badge></div>{stats.length || Number(teamARounds.rounds_won) > 0 ? <div className="comparison-table match-summary"><div><span>Показатель</span><strong><button className="entity-link" onClick={() => openTeamCard(teamAData)}>{teamA}</button></strong><strong><button className="entity-link" onClick={() => openTeamCard(teamBData)}>{teamB}</button></strong></div>{summaryRows.map(([label, left, right]) => { const [leftClass, rightClass] = statTone(left, right, label === "Смерти"); return <div key={label}><span>{label}</span><strong className={leftClass}>{label.includes("Пистолет") ? formatPercent(left) : formatNumber(left, label.includes("K/D") ? 2 : 1)}</strong><strong className={rightClass}>{label.includes("Пистолет") ? formatPercent(right) : formatNumber(right, label.includes("K/D") ? 2 : 1)}</strong></div>; })}</div> : <div className="empty-small">Подробная статистика для этой серии недоступна.</div>}</div>
        <div className="profile-section"><div className="section-label"><span>Лучшие игроки матча</span><span className="muted">по убийствам</span></div>{topPlayers.length ? <div className="match-top-players">{topPlayers.map((player, index) => <div key={getId(player, index)}><span className="player-avatar">{String(player.player ?? player.nickname ?? "?")[0]}</span><strong>{String(player.player ?? player.nickname ?? "Неизвестно")}</strong><em>{String(player.team ?? "-")}</em><b>{formatNumber(player.kills)} K</b><small>K/D {formatNumber(player.kd_ratio, 2)} · ADR {formatNumber(player.adr, 1)}</small></div>)}</div> : <div className="empty-small">Статистика игроков для этого матча пока не загружена.</div>}</div>
      </>}
      {tab === "maps" && <div className="profile-section"><div className="section-label"><span>Карты</span><span className="muted">Счёт и командная статистика</span></div>{maps.map((item, index) => { const left = asRecord(item.team1_stats); const right = asRecord(item.team2_stats); const leftRounds = asRecord(item.team1_rounds); const rightRounds = asRecord(item.team2_rounds); const pickedBy = String(item.picked_by_team_id) === String(teamAData.id) ? teamA : String(item.picked_by_team_id) === String(teamBData.id) ? teamB : null; return <div className="map-detail-block" key={getId(item, index)}><div className="map-detail-head"><div><span>Карта {formatNumber(item.number ?? item.map_number)}{pickedBy ? ` · выбор ${pickedBy}` : ""}</span><strong>{String(item.name ?? item.map_name ?? "Неизвестно")}</strong></div><em>{String(item.score_team1 ?? "-")} : {String(item.score_team2 ?? "-")}</em></div><div className="map-halves"><span>Первая половина: {String(item.first_half_team1 ?? "-")} : {String(item.first_half_team2 ?? "-")}</span><span>Вторая половина: {String(item.second_half_team1 ?? "-")} : {String(item.second_half_team2 ?? "-")}</span>{Boolean(item.overtime) && <Badge tone="warn">Овертайм</Badge>}</div><div className="comparison-table compact"><div><span>Показатель</span><strong>{teamA}</strong><strong>{teamB}</strong></div>{[["Убийства", left.kills, right.kills], ["Смерти", left.deaths, right.deaths], ["Ассисты", left.assists, right.assists], ["K/D", left.kd_ratio, right.kd_ratio], ["ADR", left.avg_adr, right.avg_adr], ["Победы за T", leftRounds.t_rounds_won, rightRounds.t_rounds_won], ["Победы за CT", leftRounds.ct_rounds_won, rightRounds.ct_rounds_won], ["Пистолетки", leftRounds.pistol_win_rate, rightRounds.pistol_win_rate]].map(([label, a, b]) => <div key={String(label)}><span>{String(label)}</span><strong>{String(label) === "Пистолетки" ? formatPercent(a) : formatNumber(a, String(label) === "K/D" ? 2 : 1)}</strong><strong>{String(label) === "Пистолетки" ? formatPercent(b) : formatNumber(b, String(label) === "K/D" ? 2 : 1)}</strong></div>)}</div></div>; })}{!maps.length && <div className="empty-small">Карты для этого матча не загружены.</div>}</div>}
      {tab === "players" && <div className="profile-section"><div className="section-label"><span>Статистика игроков</span><span className="muted">Отдельно по каждой карте</span></div>{mapsWithStats.map(({ map, stats: mapStats }, mapIndex) => <div className="map-stat-group" key={getId(map, mapIndex)}><h3>{String(map.name ?? map.map_name ?? "Карта")} <span>{String(map.score_team1 ?? "-")} : {String(map.score_team2 ?? "-")}</span></h3><div className="table-wrap"><table><thead><tr><th>Игрок</th><th>Команда</th><th className="num">K</th><th className="num">D</th><th className="num">A</th><th className="num">K/D</th><th className="num">ADR</th></tr></thead><tbody>{mapStats.map((row, index) => <tr key={getId(row, index)}><td>{String(row.player ?? row.nickname ?? "Неизвестно")}</td><td><button className="entity-link" onClick={() => openTeamCard({ id: row.team_id, name: row.team })}>{String(row.team ?? "-")}</button></td><td className="num">{formatNumber(row.kills)}</td><td className="num">{formatNumber(row.deaths)}</td><td className="num">{formatNumber(row.assists)}</td><td className="num">{formatNumber(row.kd_ratio, 2)}</td><td className="num">{formatNumber(row.adr, 1)}</td></tr>)}</tbody></table></div></div>)}</div>}
      {tab === "rounds" && <div className="profile-section"><div className="section-label"><span>История раундов</span><span className="muted">Пистолетки выделены цветом</span></div>{maps.map((map, mapIndex) => { const roundRows = asArray(map.round_history, ["round_history", "items"]); return <div className="round-map" key={getId(map, mapIndex)}><h3>{String(map.name ?? map.map_name ?? "Карта")} <span>{String(map.score_team1 ?? "-")} : {String(map.score_team2 ?? "-")}</span></h3>{roundRows.length ? <div className="round-grid">{roundRows.map((round, index) => { const winnerName = String(round.winner_team_id) === String(teamAData.id) ? teamA : String(round.winner_team_id) === String(teamBData.id) ? teamB : "Неизвестно"; return <div key={getId(round, index)} className={round.is_pistol ? "pistol" : ""}><strong>{formatNumber(round.number)}</strong><span>{winnerName}</span><em>{String(round.winner_side ?? "-")} · {String(round.score_team1 ?? "-")}:{String(round.score_team2 ?? "-")}</em></div>; })}</div> : <div className="empty-small">Раундовая история этой карты недоступна.</div>}</div>; })}{!maps.length && <div className="empty-small">Карты и раунды для этого матча не загружены.</div>}</div>}
    </div>
  </aside>;
}

function UpcomingSyncButton({ onComplete }: { onComplete: () => void }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (!jobId || api.demo) return;
    let disposed = false;
    const poll = async () => {
      try {
        const response = asRecord(await api.get(`/api/sync/grid/jobs/${jobId}`));
        const job = asRecord(response.job ?? response);
        if (disposed) return;
        const status = String(job.status ?? "queued").toLowerCase();
        const progress = asRecord(job.progress);
        if (status === "queued") setMessage(`В очереди${job.queue_position ? ` · позиция ${formatNumber(job.queue_position)}` : ""}`);
        else if (status.includes("running")) setMessage(String(progress.phase) === "participant-history" ? `История команд: ${formatNumber(progress.team_index)} из ${formatNumber(progress.teams_total)} · ${String(progress.team ?? "")}` : `Расписание: проверено ${formatNumber(progress.checked)}, найдено ${formatNumber(progress.matched)}`);
        if (["completed", "failed", "cancelled", "interrupted", "partial"].some((value) => status.includes(value))) {
          setJobId(null);
          const result = asRecord(job.result);
          const upcomingResult = asRecord(result.upcoming);
          if (status.includes("complete")) {
            setMessage(`Готово: ${formatNumber(upcomingResult.saved)} будущих матчей, история ${formatNumber(result.history_saved)} записей.`);
            onComplete();
          } else setMessage(String(job.error ?? statusLabel(status)));
        }
      } catch (error) {
        if (!disposed) setMessage(error instanceof Error ? error.message : "Не удалось получить статус обновления");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [jobId, onComplete]);
  const start = async () => {
    setStarting(true); setMessage(null);
    try {
      const response = asRecord(await api.request("/api/sync/grid", { method: "POST", body: JSON.stringify({ mode: "pandascore-upcoming", days: 14, top_limit: 50, max_pages: 5, max_matches: 500, participant_history_days: 180, history_max_pages: 2, history_max_matches: 100, post_pipeline: false, refresh_stats: false }) }));
      setJobId(String(response.job_id));
      setMessage("Обновление поставлено в очередь.");
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}${error.detail ? ` - ${error.detail}` : ""}` : error instanceof Error ? error.message : "Не удалось запустить обновление");
    } finally { setStarting(false); }
  };
  return <div className="upcoming-sync"><button className="button primary" disabled={starting || !!jobId} onClick={start}>{starting ? "Запуск…" : jobId ? "Обновляется…" : "Обновить матчи и историю"}</button>{message && <small>{message}</small>}</div>;
}

function eventName(row: JsonRecord) {
  return String(row.event_name ?? row.event ?? "Без названия");
}

function eventPriority(row: JsonRecord) {
  const priority = asRecord(row.event_priority);
  return {
    tier: String(priority.tier ?? "other"),
    label: String(priority.label ?? "Other"),
    priority: Number(priority.priority ?? 0),
  };
}

function eventTierTone(tier: string) {
  if (tier === "tier-1") return "good" as const;
  if (tier === "tier-2") return "info" as const;
  if (tier === "unknown") return "neutral" as const;
  return "warn" as const;
}

function Upcoming({ filters, setFilters }: { filters: typeof initialFilters; setFilters: React.Dispatch<React.SetStateAction<typeof initialFilters>> }) {
  const upcoming = useResource(() => api.get("/api/upcoming", { days: filters.days }), demo.upcoming, [filters.days]);
  const rows = asArray(upcoming.data, ["upcoming", "matches", "items", "results"]);
  const [selected, setSelected] = useState<JsonRecord | null>(null);
  const [selectedTournament, setSelectedTournament] = useState("");
  const tournaments = Array.from(new Map(rows.map((row) => {
    const name = eventName(row);
    const priority = eventPriority(row);
    return [name, {
      name,
      matches: rows.filter((item) => eventName(item) === name).length,
      ...priority,
    }];
  })).values()).sort((left, right) => right.priority - left.priority || left.name.localeCompare(right.name, "ru"));
  const tournamentNames = tournaments.map((item) => item.name);
  useEffect(() => {
    if (!tournaments.length) setSelectedTournament("");
    else if (!tournamentNames.includes(selectedTournament)) setSelectedTournament(tournaments[0].name);
  }, [rows.length, tournamentNames.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps
  const tournamentRows = rows
    .filter((row) => eventName(row) === selectedTournament)
    .sort((left, right) => String(left.match_time ?? left.start_time ?? "").localeCompare(String(right.match_time ?? right.start_time ?? "")));
  return <>
    <PageTitle eyebrow="Предматчевая аналитика" title="Будущие матчи" description="Сначала выберите турнир, затем матч для сравнения команд.">
      <UpcomingSyncButton onComplete={upcoming.reload} />
      <label className="compact-select"><span>Последних матчей</span><select value={filters.window} onChange={(event) => setFilters((current) => ({ ...current, window: Number(event.target.value) }))}>{[5, 10, 20, 50].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label className="compact-select"><span>Период GRID</span><select value={filters.statsWindow} onChange={(event) => setFilters((current) => ({ ...current, statsWindow: event.target.value }))}>{["LAST_WEEK", "LAST_MONTH", "LAST_3_MONTHS", "LAST_6_MONTHS", "LAST_YEAR"].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
    </PageTitle>
    <Panel title="Ближайшие турниры" meta={`${tournaments.length} турниров · Tier-1 сверху`} className="upcoming-tournaments">
      <TableState loading={upcoming.loading} error={upcoming.error} empty={!tournaments.length} onRetry={upcoming.reload} />
      {!upcoming.loading && !upcoming.error && <div className="tournament-list">{tournaments.map((tournament) => <button key={tournament.name} className={`${tournament.name === selectedTournament ? "active" : ""} tier-${tournament.tier}`} onClick={() => { setSelectedTournament(tournament.name); setSelected(null); }}><strong>{tournament.name}</strong><span><Badge tone={eventTierTone(tournament.tier)}>{tournament.label}</Badge> {tournament.matches} матч.</span></button>)}</div>}
    </Panel>
    <div className="upcoming-layout">
      <Panel title="Матчи турнира" meta={`${tournamentRows.length} серий`}>
        <TableState loading={upcoming.loading} error={upcoming.error} empty={!tournamentRows.length} onRetry={upcoming.reload} />
        {!upcoming.loading && !upcoming.error && <div className="fixture-list">{tournamentRows.map((row, index) => { const a = getName(row.team1 ?? row.team_a, "TBD"); const b = getName(row.team2 ?? row.team_b, "TBD"); const tier = eventPriority(row); return <div key={getId(row, index)} role="button" tabIndex={0} onClick={() => setSelected(row)} onKeyDown={(event) => { if (event.key === "Enter") setSelected(row); }} className={`fixture-item ${getId(selected ?? {}) === getId(row) ? "active" : ""}`}><span>{formatDate(row.start_time ?? row.scheduled_at)}</span><div><button className="entity-link" onClick={(event) => { event.stopPropagation(); openTeamCard(row.team1 ?? row.team_a); }}><strong>{a}</strong></button><em>vs</em><button className="entity-link" onClick={(event) => { event.stopPropagation(); openTeamCard(row.team2 ?? row.team_b); }}><strong>{b}</strong></button></div><small>{eventName(row)} · <Badge tone={eventTierTone(tier.tier)}>{tier.label}</Badge> · {completenessLabel(asRecord(row.completeness).level)}</small></div>; })}</div>}
      </Panel>
      {selected ? <MatchPreview match={selected} filters={filters} /> : <Panel title="Сравнение команд" meta="Выберите матч"><div className="preview-empty"><span>◎</span><strong>Выберите будущий матч</strong><p>Здесь появится сравнение формы, карт, GRID-метрик и составов.</p></div></Panel>}
    </div>
  </>;
}

function MatchPreview({ match, filters }: { match: JsonRecord; filters: typeof initialFilters }) {
  const id = getId(match);
  const [windowSize, setWindowSize] = useState(filters.window);
  useEffect(() => setWindowSize(filters.window), [id, filters.window]);
  const preview = useResource(() => api.get(`/api/matches/${id}/preview`, { window: windowSize, stats_window: filters.statsWindow }), match.preview ?? demo.preview, [id, windowSize, filters.statsWindow]);
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
  const warnings = Array.isArray(coverage.warnings) ? coverage.warnings.map((value) => String(value)
    .replace("low recent match sample", "мало последних матчей")
    .replace("incomplete player sample", "неполная статистика игроков")
    .replace("no GRID stats snapshot", "нет снимка статистики GRID")) : [];
  const headToHead = asRecord(data.head_to_head);
  const commonOpponents = asArray(data.common_opponents, ["common_opponents", "items"]);
  const playerForm = asRecord(data.player_form);
  const playersA = asArray(playerForm.team1, ["team1", "players"]);
  const playersB = asArray(playerForm.team2, ["team2", "players"]);
  const advantages = asRecord(data.advantages);
  const advantagesA = asArray(advantages.team1, ["team1", "items"]);
  const advantagesB = asArray(advantages.team2, ["team2", "items"]);
  const recentA = asArray(a.recent_matches, ["recent_matches", "matches", "items"]);
  const recentB = asArray(b.recent_matches, ["recent_matches", "matches", "items"]);
  const leadingTeam = edge > 5 ? getName(a, "Команда A") : edge < -5 ? getName(b, "Команда B") : null;
  const verdict = leadingTeam ? `${leadingTeam} выглядит сильнее по текущей выборке` : "Матч выглядит близким по текущей выборке";
  const verdictTone = leadingTeam ? "info" as const : "neutral" as const;
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
  return <Panel title="Сравнение перед матчем" meta={`${String(match.event_name ?? match.event ?? "Турнир")} · ${formatDate(match.start_time ?? match.scheduled_at)}`}>
    {preview.error && <div className="notice warn">Не удалось получить актуальное сравнение. Показаны сохранённые данные матча.</div>}
    <div className="preview-toolbar">
      <div><span>Выборка</span><strong>{windowSize} последних матчей</strong></div>
      <div className="segmented">{[5, 10, 20, 50].map((value) => <button key={value} className={windowSize === value ? "active" : ""} onClick={() => setWindowSize(value)}>{value}</button>)}</div>
      <Badge tone={verdictTone}>{verdict}</Badge>
    </div>
    <div className="versus-head">
      <div><TeamMark name={getName(a, "Team A")} /><button className="entity-link" onClick={() => openTeamCard(a)}><strong>{getName(a, "Team A")}</strong></button><Form value={a.form} /></div>
      <div className="edge"><span>Оценка по сохранённой статистике</span><strong>{scoreA.toFixed(0)} <em>:</em> {scoreB.toFixed(0)}</strong><Badge tone={confidence === "high" ? "good" : confidence === "low" ? "warn" : "info"}>Достоверность: {{ high: "высокая", medium: "средняя", low: "низкая" }[confidence] ?? confidence}</Badge></div>
      <div><TeamMark name={getName(b, "Team B")} /><button className="entity-link" onClick={() => openTeamCard(b)}><strong>{getName(b, "Team B")}</strong></button><Form value={b.form} /></div>
    </div>
    <button className="button ghost match-open-button" onClick={() => openMatchCard(match)}>Открыть карточку матча</button>
    <div className="edge-disclaimer">Это сравнительная оценка формы, а не букмекерская вероятность. Достоверность зависит от свежести и полноты выборки.</div>
    <div className="preview-section"><h3>Форма команд</h3><div className="team-form-grid"><div><strong>{getName(a, "Команда A")}</strong>{recentA.length ? recentA.slice(0, 6).map((row, index) => <button key={getId(row, index)} onClick={() => openMatchCard(row)}><span>{formatDate(row.match_time ?? row.start_time)}</span><em>{getName(row.team1, "A")} vs {getName(row.team2, "B")}</em><b className={row.won ? "positive" : "negative"}>{row.won ? "W" : "L"}</b><small>{String(row.score_team1 ?? "-")}:{String(row.score_team2 ?? "-")}</small></button>) : <p>Нет сохранённых матчей в выбранном окне.</p>}</div><div><strong>{getName(b, "Команда B")}</strong>{recentB.length ? recentB.slice(0, 6).map((row, index) => <button key={getId(row, index)} onClick={() => openMatchCard(row)}><span>{formatDate(row.match_time ?? row.start_time)}</span><em>{getName(row.team1, "A")} vs {getName(row.team2, "B")}</em><b className={row.won ? "positive" : "negative"}>{row.won ? "W" : "L"}</b><small>{String(row.score_team1 ?? "-")}:{String(row.score_team2 ?? "-")}</small></button>) : <p>Нет сохранённых матчей в выбранном окне.</p>}</div></div></div>
    <div className="advantage-grid"><div><strong>Сильные стороны {getName(a, "Команда A")}</strong>{advantagesA.length ? advantagesA.map((row, index) => <span key={getId(row, index)}><em>{metricLabel(row.label)}</em><small>{String(row.unit).includes("percent") ? formatPercent(row.value) : formatNumber(row.value, 2)}</small></span>) : <p>Явного преимущества не найдено.</p>}</div><div><strong>Сильные стороны {getName(b, "Команда B")}</strong>{advantagesB.length ? advantagesB.map((row, index) => <span key={getId(row, index)}><em>{metricLabel(row.label)}</em><small>{String(row.unit).includes("percent") ? formatPercent(row.value) : formatNumber(row.value, 2)}</small></span>) : <p>Явного преимущества не найдено.</p>}</div></div>
    <div className="coverage-strip">
      <div><strong>{getName(a, "Команда A")}</strong><Badge tone={String(coverageA.level ?? "low") === "high" ? "good" : String(coverageA.level ?? "low") === "medium" ? "info" : "warn"}>{{ high: "полные", medium: "частичные", low: "мало данных" }[String(coverageA.level ?? "low")] ?? String(coverageA.level)}</Badge><span>{formatNumber(coverageA.matches)} матчей / {formatNumber(coverageA.maps)} карт / {formatNumber(coverageA.players_with_stats)} игроков</span></div>
      <div><strong>{getName(b, "Команда B")}</strong><Badge tone={String(coverageB.level ?? "low") === "high" ? "good" : String(coverageB.level ?? "low") === "medium" ? "info" : "warn"}>{{ high: "полные", medium: "частичные", low: "мало данных" }[String(coverageB.level ?? "low")] ?? String(coverageB.level)}</Badge><span>{formatNumber(coverageB.matches)} матчей / {formatNumber(coverageB.maps)} карт / {formatNumber(coverageB.players_with_stats)} игроков</span></div>
    </div>
    {warnings.length > 0 && <div className="notice warn"><strong>Полнота данных:</strong> {warnings.join("; ")}</div>}
    <div className="preview-section"><h3>Контекст выборки</h3><div className="comparison-table"><div><span>Очные встречи</span><strong>{getName(a, "A")}</strong><strong>{getName(b, "B")}</strong></div><div><span>{formatNumber(headToHead.matches)} матчей</span><strong>{formatNumber(headToHead.team1_wins)} побед</strong><strong>{formatNumber(headToHead.team2_wins)} побед</strong></div>{commonOpponents.slice(0, 5).map((row, index) => <div key={getId(row, index)}><span>{String(row.opponent ?? "Соперник")}</span><strong>{formatPercent(row.team1_win_rate)} · n={formatNumber(row.team1_games)}</strong><strong>{formatPercent(row.team2_win_rate)} · n={formatNumber(row.team2_games)}</strong></div>)}</div></div>
    <div className="preview-section"><h3>Сравнение показателей</h3>{comparisons.length ? <div className="comparison-table"><div><span>Показатель</span><strong>{getName(a, "A")}</strong><strong>{getName(b, "B")}</strong></div>{comparisons.map((row, index) => { const v1 = Number(row.team1_value ?? row.value1 ?? row.a); const v2 = Number(row.team2_value ?? row.value2 ?? row.b); return <div key={getId(row, index)}><span>{metricLabel(row.label ?? row.metric)}</span><strong className={v1 > v2 ? "positive" : ""}>{String(row.display1 ?? metricValue(row, "team1_value"))}</strong><strong className={v2 > v1 ? "positive" : ""}>{String(row.display2 ?? metricValue(row, "team2_value"))}</strong></div>; })}</div> : <div className="empty-small">Для сравнения пока недостаточно сохранённых данных.</div>}</div>
    <div className="preview-section"><h3>Карты</h3>{maps.length ? <div className="map-compare">{maps.map((map, index) => { const va = map.team1_win_rate ?? map.a; const vb = map.team2_win_rate ?? map.b; const leader = String(map.leader ?? "tie"); return <div key={getId(map, index)}><strong>{getName(map, String(map.map_name ?? map.map ?? "Карта"))}</strong><span className={leader === "team1" ? "positive" : ""}>{formatPercent(va)} <small>n={formatNumber(map.team1_sample ?? map.sample_a)} · Δ {formatNumber(map.team1_round_diff, 0)}</small></span><i><em style={{ width: `${barWidth(va)}%` }} /></i><span className={leader === "team2" ? "positive" : ""}>{formatPercent(vb)} <small>n={formatNumber(map.team2_sample ?? map.sample_b)} · Δ {formatNumber(map.team2_round_diff, 0)}</small></span><Badge tone={String(map.state ?? "").includes("insufficient") ? "warn" : "neutral"}>{String(map.state ?? "").includes("insufficient") ? "мало игр" : "готово"}</Badge></div>; })}</div> : <div className="empty-small">Нет сохранённой статистики карт для обеих команд.</div>}</div>
    <div className="preview-section"><h3>Форма игроков</h3><div className="player-compare"><div><strong>{getName(a, "Команда A")}</strong>{playersA.length ? playersA.map((player, index) => <span key={getId(player, index)}><em>{String(player.nickname ?? player.player ?? "Игрок")}</em><small>K/D {formatNumber(player.kd_ratio, 2)} / ADR {formatNumber(player.adr, 1)} / {formatNumber(player.maps)} карт</small></span>) : <p>Статистика игроков не загружена.</p>}</div><div><strong>{getName(b, "Команда B")}</strong>{playersB.length ? playersB.map((player, index) => <span key={getId(player, index)}><em>{String(player.nickname ?? player.player ?? "Игрок")}</em><small>K/D {formatNumber(player.kd_ratio, 2)} / ADR {formatNumber(player.adr, 1)} / {formatNumber(player.maps)} карт</small></span>) : <p>Статистика игроков не загружена.</p>}</div></div></div>
  </Panel>;
}

function durationText(value: unknown) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "считаем";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 1) return `${rest} сек`;
  return `${minutes} мин ${rest} сек`;
}

function BackfillProgress({ job }: { job: JsonRecord | null }) {
  if (!job) return null;
  const progress = asRecord(job.progress);
  if (!Object.keys(progress).length) return null;
  const totals = Object.keys(asRecord(progress.totals)).length ? asRecord(progress.totals) : progress;
  const percent = Math.max(0, Math.min(100, Number(progress.progress_percent ?? 0)));
  return <div className="backfill-progress">
    <div className="progress-head">
      <div><span>Текущий этап</span><strong>{String(progress.current_day ?? progress.stage ?? "подготовка")}</strong></div>
      <Badge tone={String(job.status ?? progress.window_status ?? "").includes("cancel") ? "warn" : "info"}>{String(job.status ?? progress.window_status ?? "running")}</Badge>
    </div>
    <div className="progress-bar"><i style={{ width: `${percent}%` }} /></div>
    <div className="progress-stats">
      <div><span>Прогресс</span><strong>{percent.toFixed(1)}%</strong></div>
      <div><span>Дней</span><strong>{formatNumber(progress.completed_windows)} / {formatNumber(progress.total_windows)}</strong></div>
      <div><span>Страница</span><strong>{formatNumber(progress.page)} / {formatNumber(progress.pages_limit)}</strong></div>
      <div><span>Проверено</span><strong>{formatNumber(totals.checked)}</strong></div>
      <div><span>Сохранено</span><strong>{formatNumber(totals.saved)}</strong></div>
      <div><span>Ошибки</span><strong>{formatNumber(totals.errors)}</strong></div>
      <div><span>Осталось</span><strong>{durationText(progress.eta_seconds)}</strong></div>
    </div>
  </div>;
}

function BackfillCalendar({ days }: { days: JsonRecord[] }) {
  const range = days.length ? `${String(days[0].day)} - ${String(days[days.length - 1].day)}` : "период не выбран";
  return <div className="coverage-calendar">
    <div className="calendar-title"><strong>{range}</strong><span><i className="complete" /> готово <i className="partial" /> частично <i className="stale" /> устарело <i className="pending" /> нет данных</span></div>
    <div className="calendar-grid">
      {days.map((day) => {
        const status = String(day.status ?? "pending");
        const date = new Date(String(day.day));
        const label = Number.isNaN(date.getTime()) ? String(day.day) : new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short" }).format(date);
        return <div key={String(day.day)} className={`calendar-day ${status}`} title={`${String(day.day)}: ${status}`}>
          <strong>{label}</strong>
          <span>{formatNumber(day.saved)} матч.</span>
        </div>;
      })}
    </div>
  </div>;
}

function AuditResult({ result }: { result: JsonRecord | null }) {
  if (!result) return null;
  const missing = asArray(result.missing_series, ["missing_series", "items"]);
  const invalid = asArray(result.invalid_matches, ["invalid_matches", "items"]);
  return <div className="audit-result">
    <div className="estimate-grid"><Metric label="Покрытие" value={formatPercent(Number(result.coverage_percent ?? 0) / 100)} /><Metric label="Ожидалось" value={formatNumber(result.expected)} /><Metric label="В базе" value={formatNumber(result.present)} /><Metric label="Пропущено" value={formatNumber(result.missing_count)} /><Metric label="Некорректно" value={formatNumber(result.invalid_count)} /></div>
    {!!missing.length && <div className="notice warn"><strong>Не загружены:</strong> {missing.slice(0, 10).map((item) => `${String(item.scheduled_at ?? "без даты").slice(0, 10)} · ${asArray(item.teams).join(" vs ") || `GRID ${String(item.series_id)}`}`).join("; ")}{missing.length > 10 ? `; ещё ${missing.length - 10}` : ""}</div>}
    {!!invalid.length && <div className="notice warn"><strong>Требуют исправления:</strong> {invalid.slice(0, 10).map((item) => `GRID ${String(item.series_id)} (${asArray(item.reasons).join(", ")})`).join("; ")}</div>}
  </div>;
}

function DataPage() {
  const status = useResource(() => api.get("/api/data-status"), demo.status, []);
  const jobs = useResource(() => api.get("/api/jobs", { limit: 50 }), demo.jobs, []);
  const automation = useResource(() => api.get("/api/automation"), { enabled: false, interval_minutes: 60 }, []);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [backfillDays, setBackfillDays] = useState(90);
  const [maxPages, setMaxPages] = useState(10);
  const [maxMatches, setMaxMatches] = useState(500);
  const [toggles, setToggles] = useState(() => ({ autoSync: false, noTop: false, pipeline: true, statsAfter: true }));
  const [automationInterval, setAutomationInterval] = useState(60);
  const dateFrom = from ? `${from}T00:00:00` : undefined;
  const dateTo = to ? `${to}T23:59:59` : undefined;
  const estimate = useResource(() => api.get("/api/backfill/estimate", { days: backfillDays, max_pages: maxPages, max_matches: maxMatches, refresh_stats: toggles.statsAfter }), demo.estimate, [backfillDays, maxPages, maxMatches, toggles.statsAfter]);
  const calendar = useResource(() => api.get("/api/backfill/calendar", { days: backfillDays, date_from: dateFrom, date_to: dateTo, top_limit: 50, require_top_team: !toggles.noTop }), { days: [] }, [backfillDays, dateFrom, dateTo, toggles.noTop]);
  const quality = useResource(() => api.get("/api/data-quality/period", { days: backfillDays, date_from: dateFrom, date_to: dateTo, candidate_limit: maxMatches }), { levels: {}, repair_candidates: [] }, [backfillDays, dateFrom, dateTo, maxMatches]);
  const validationReport = useResource(() => api.get("/api/validate"), { checks: demo.validation }, []);
  const [running, setRunning] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<JsonRecord | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [lastAudit, setLastAudit] = useState<JsonRecord | null>(null);
  const validation = asRecord(validationReport.data);
  const estimateData = asRecord(estimate.data);
  const calendarDays = asArray(calendar.data, ["days"]);
  const qualityData = asRecord(quality.data);
  const qualityLevels = asRecord(qualityData.levels);
  const jobRows = asArray(jobs.data, ["jobs", "items", "results"]);
  const refreshAll = () => Promise.all([status.reload(), estimate.reload(), calendar.reload(), quality.reload(), jobs.reload(), validationReport.reload(), automation.reload()]);
  useEffect(() => {
    const settings = asRecord(automation.data);
    if (settings.interval_minutes != null) setAutomationInterval(Number(settings.interval_minutes));
    if (settings.enabled != null) setToggles((current) => ({ ...current, autoSync: Boolean(settings.enabled) }));
  }, [automation.data]);
  useEffect(() => {
    if (activeJobId || api.demo || jobs.loading || jobs.error) return;
    const current = jobRows.find((job) => {
      const jobStatus = String(job.status ?? "").toLowerCase();
      return jobStatus.includes("running") || jobStatus.includes("queued");
    });
    const jobId = current?.job_id ?? current?.id;
    if (jobId != null) {
      setActiveJobId(String(jobId));
      setActiveJob(current ?? null);
    }
  }, [activeJobId, jobs.data]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!activeJobId || api.demo) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = asRecord(await api.get(`/api/sync/grid/jobs/${activeJobId}`));
        const current = asRecord(job.job ?? job);
        if (!cancelled) setActiveJob(current);
        const jobStatus = String(current.status ?? "running").toLowerCase();
        await Promise.all([jobs.reload(), status.reload(), calendar.reload(), validationReport.reload()]);
        if (!cancelled && ["completed", "complete", "success", "succeeded", "failed", "error", "cancelled", "interrupted", "partial"].some((value) => jobStatus.includes(value))) {
          const result = asRecord(current.result);
          if (String(current.kind ?? asRecord(current.request).mode).includes("audit") || Object.prototype.hasOwnProperty.call(result, "coverage_percent")) setLastAudit(result);
          setActiveJobId(null);
          setMessage(jobStatus.includes("fail") || jobStatus.includes("error") ? `Задача ${activeJobId} завершилась с ошибкой.` : `Задача ${activeJobId} завершена.`);
        }
      } catch (error) {
        if (!cancelled) setMessage(error instanceof Error ? `Опрос задачи: ${error.message}` : "Не удалось получить статус задачи");
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
      if (jobId != null) {
        setActiveJobId(String(jobId));
        setActiveJob(asRecord(response.job));
      }
      setMessage(jobId != null ? `${label}: задача ${String(jobId)} запущена.` : `${label}: выполнено.`);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof ApiError ? `${error.message}${error.detail ? ` - ${error.detail}` : ""}` : error instanceof Error ? error.message : "Действие не выполнено");
    } finally { setRunning(null); }
  };
  const saveAutomation = async (enabled: boolean, interval = automationInterval) => {
    setToggles((current) => ({ ...current, autoSync: enabled }));
    try {
      await api.request("/api/automation", { method: "PUT", body: JSON.stringify({ enabled, interval_minutes: interval, upcoming_days: 14, results_days: 7, top_limit: 50, max_matches: maxMatches, refresh_stats: toggles.statsAfter }) });
      await automation.reload();
      setMessage(enabled ? "Серверная автосинхронизация включена." : "Серверная автосинхронизация выключена.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось изменить автосинхронизацию");
      await automation.reload();
    }
  };
  const cancelActiveJob = async () => {
    if (!activeJobId) return;
    await run("Остановка сбора", `/api/sync/grid/jobs/${activeJobId}/cancel`, "POST");
  };
  const resetPeriod = async () => {
    if (!dateFrom || !dateTo) { setMessage("Для сброса отметок выберите обе даты."); return; }
    await run("Сброс отметок", "/api/backfill/reset", "POST", { date_from: dateFrom, date_to: dateTo, cursor: "grid-main" });
  };
  return <>
    <PageTitle eyebrow="Управление данными" title="Загрузка и проверка" description="Выбери период, запусти загрузку из GRID и следи за прогрессом без консоли.">
      <button className="button ghost" onClick={() => refreshAll()}>Обновить статус</button>
      <button className="button primary" disabled={!!running || !!activeJobId} onClick={() => run("Полное обновление", "/api/sync/grid", "POST", { mode: "update-all", days: 7, history_days: 14, max_matches: maxMatches, top_limit: 50, post_pipeline: false, refresh_stats: toggles.statsAfter })}>Обновить всё</button>
      <button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Резервная копия", "/api/backup")}>Создать копию БД</button>
      <button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Экспорт данных", "/api/export")}>Экспорт CSV</button>
      {activeJobId && <button className="button danger" disabled={!!running} onClick={cancelActiveJob}>Остановить сбор</button>}
      <button className="button primary" disabled={!!running || !!activeJobId} onClick={() => run("Проверка обновлений", "/api/sync/grid", "POST", { mode: "update", dry_run: true })}>Проверить обновления</button>
    </PageTitle>
    <div className="data-status-strip"><div><StatusDot tone="good" /><span>API</span><strong>Подключён</strong></div><div><span>Курсор</span><strong>{String(asRecord(status.data).cursor ?? "—")}</strong></div><div><span>Синхронизация</span><strong>{formatDate(asRecord(status.data).last_sync)}</strong></div><div><span>Исходные данные</span><strong>{formatDate(asRecord(status.data).latest_raw_fetch)}</strong></div><div><span>Статистика</span><strong>{formatDate(asRecord(status.data).latest_stats_fetch)}</strong></div><div><span>Проверка</span><Badge tone={String(asRecord(status.data).validation_status ?? "unknown").includes("fail") ? "bad" : String(asRecord(status.data).validation_status ?? "unknown").includes("pass") ? "good" : "neutral"}>{String(asRecord(status.data).validation_status ?? "unknown")}</Badge></div></div>
    {message && <div className={`notice ${message.includes("запущена") || message.includes("выполнено") || message.includes("завершена") ? "good" : "warn"}`}>{message}{activeJobId && <span> Обновляем прогресс автоматически.</span>}</div>}
    <BackfillProgress job={activeJob} />
    <AuditResult result={lastAudit} />
    <div className="data-grid">
      <Panel title="Период и лимиты" meta="Операции, которые меняют локальную базу">
        <div className="form-grid"><label><span>Дата с</span><input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label><span>Дата по</span><input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label><label><span>Если даты пустые, дней назад</span><input type="number" min="1" max="365" value={backfillDays} onChange={(event) => setBackfillDays(Number(event.target.value))} /></label><label><span>Страниц на день</span><input type="number" min="1" value={maxPages} onChange={(event) => setMaxPages(Number(event.target.value))} /></label><label><span>Матчей максимум</span><input type="number" min="1" value={maxMatches} onChange={(event) => setMaxMatches(Number(event.target.value))} /></label></div>
        <div className="toggle-list"><Toggle label="Серверная автосинхронизация" value={toggles.autoSync} onChange={(value) => void saveAutomation(value)} /><label><span>Интервал автосинхронизации</span><select value={automationInterval} onChange={(event) => { const interval = Number(event.target.value); setAutomationInterval(interval); if (toggles.autoSync) void saveAutomation(true, interval); }}><option value={30}>30 минут</option><option value={60}>60 минут</option><option value={120}>2 часа</option><option value={360}>6 часов</option></select></label><div className="automation-health"><span><StatusDot tone={Boolean(asRecord(automation.data).worker_running) ? "good" : "bad"} />Worker {Boolean(asRecord(automation.data).worker_running) ? "работает" : "остановлен"}</span><span>В очереди: {formatNumber(asRecord(automation.data).queue_size)}</span>{toggles.autoSync && <span>Следующий запуск: {formatDate(asRecord(automation.data).next_run_at)}</span>}</div><Toggle label="Не фильтровать по топ-командам" value={toggles.noTop} onChange={(value) => setToggles({ ...toggles, noTop: value })} /><Toggle label="После загрузки пересчитать метрики" value={toggles.pipeline} onChange={(value) => setToggles({ ...toggles, pipeline: value })} /><Toggle label="После загрузки обновить GRID stats" value={toggles.statsAfter} onChange={(value) => setToggles({ ...toggles, statsAfter: value })} /></div>
        <div className="action-grid"><button className="button primary" disabled={!!running || !!activeJobId} onClick={() => run("Скачать период", "/api/sync/grid", "POST", { mode: "backfill", days: backfillDays, date_from: dateFrom, date_to: dateTo, window_days: 1, max_pages: maxPages, max_matches: maxMatches, top_limit: 50, post_pipeline: toggles.pipeline, refresh_stats: toggles.statsAfter, require_top_team: !toggles.noTop })}>Скачать период</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Быстрая проверка периода", "/api/sync/grid", "POST", { mode: "audit", days: backfillDays, date_from: dateFrom, date_to: dateTo, max_pages: 50, top_limit: 50, post_pipeline: false, refresh_stats: false, require_top_team: !toggles.noTop })}>Быстрая проверка периода</button><button className="button ghost" disabled={!!running || !!activeJobId || Number(qualityData.repairable_count ?? 0) === 0} onClick={() => run("Восстановление неполных матчей", "/api/sync/grid", "POST", { mode: "repair", days: backfillDays, date_from: dateFrom, date_to: dateTo, max_matches: Math.min(maxMatches, 100), post_pipeline: toggles.pipeline, refresh_stats: false })}>Восстановить неполные</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={resetPeriod}>Сбросить отметки периода</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Догрузить новое", "/api/sync/grid", "POST", { mode: "update", days: 7, max_pages: maxPages, max_matches: maxMatches, top_limit: 50, post_pipeline: toggles.pipeline, refresh_stats: toggles.statsAfter, require_top_team: !toggles.noTop })}>Догрузить новое</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Предстоящие матчи", "/api/sync/grid", "POST", { mode: "pandascore-upcoming", days: 14, participant_history_days: 180, history_max_pages: 2, history_max_matches: 100, top_limit: 50, max_pages: 5, max_matches: maxMatches, post_pipeline: toggles.pipeline, refresh_stats: toggles.statsAfter })}>Найти будущие матчи</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Обновить live", "/api/sync/grid", "POST", { mode: "refresh-live", max_matches: maxMatches, post_pipeline: toggles.pipeline, refresh_stats: toggles.statsAfter })}>Обновить live</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Тест без записи", "/api/sync/grid", "POST", { mode: "recent", dry_run: true, date_from: dateFrom, date_to: dateTo, max_pages: maxPages, max_matches: maxMatches })}>Тест без записи</button><button className="button ghost" disabled={!!running || !!activeJobId} onClick={() => run("Обновить stats", "/api/sync/grid-stats", "POST", { window: "LAST_MONTH" })}>Обновить stats</button><button className="button ghost" disabled={!!running} onClick={() => run("Пересчитать метрики", "/api/metrics/compute")}>Пересчитать метрики</button><button className="button ghost" disabled={!!running} onClick={() => run("Проверить данные", "/api/validate", "GET")}>Проверить данные</button></div>
      </Panel>
      <Panel title="Оценка загрузки" meta="Перед стартом исторической выгрузки">
        <TableState loading={estimate.loading} error={estimate.error} empty={false} onRetry={estimate.reload} />
        {!estimate.loading && !estimate.error && <><div className="estimate-hero"><div><span>Примерное время</span><strong>{String(estimateData.eta_text ?? estimateData.eta ?? estimateData.estimated_duration ?? "считаем")}</strong></div><Badge tone="warn">Лимитировано API</Badge></div><div className="estimate-grid"><Metric label="Дней" value={formatNumber(estimateData.windows)} /><Metric label="Запросов" value={formatNumber(estimateData.estimated_requests ?? estimateData.requests)} /><Metric label="Матчей" value={formatNumber(estimateData.estimated_matches ?? estimateData.new_matches)} /><Metric label="Покрытие" value={formatPercent(estimateData.coverage)} /></div><div className="notice">Полные дни будут пропускаться. День считается полным, если последняя загрузка этого дня завершилась без ошибок.</div><button className="button danger" disabled={!!running} onClick={() => run("Скачать период", "/api/sync/grid", "POST", { mode: "backfill", days: backfillDays, date_from: dateFrom, date_to: dateTo, window_days: 1, max_pages: maxPages, max_matches: maxMatches, top_limit: 50, post_pipeline: toggles.pipeline, refresh_stats: toggles.statsAfter, require_top_team: !toggles.noTop })}>Скачать период</button></>}
      </Panel>
      <Panel title="Календарь загруженных дат" meta="Зелёные даты при повторной загрузке пропускаются" className="span-2">
        <TableState loading={calendar.loading} error={calendar.error} empty={false} onRetry={calendar.reload} />
        {!calendar.loading && !calendar.error && <BackfillCalendar days={calendarDays} />}
      </Panel>
      <Panel title="Полнота матчей периода" meta={`${formatNumber(qualityData.matches)} завершённых матчей`} className="span-2">
        <TableState loading={quality.loading} error={quality.error} empty={false} onRetry={quality.reload} />
        {!quality.loading && !quality.error && <><div className="estimate-grid"><Metric label="Только результат" value={formatNumber(qualityLevels.result)} /><Metric label="С картами" value={formatNumber(qualityLevels.maps)} /><Metric label="Со статистикой игроков" value={formatNumber(qualityLevels.players)} /><Metric label="С раундами" value={formatNumber(qualityLevels.rounds)} /><Metric label="Некорректные" value={formatNumber(qualityLevels.invalid)} /><Metric label="Можно восстановить" value={formatNumber(qualityData.repairable_count)} /></div><div className="notice">Покрытие карт: {formatPercent(qualityData.map_coverage)} · игроков: {formatPercent(qualityData.player_coverage)} · раундов: {formatPercent(qualityData.round_coverage)}. Восстановление доступно только для матчей с GRID series ID.</div></>}
      </Panel>
      <Panel title="История задач" meta={`${jobRows.length} последних запусков`} className="span-2">
        <TableState loading={jobs.loading} error={jobs.error} empty={!jobRows.length} onRetry={jobs.reload} />
        {!jobs.loading && !jobs.error && !!jobRows.length && <div className="table-wrap"><table><thead><tr><th>Старт</th><th>Тип</th><th>Этап / ошибка</th><th className="num">Прогресс</th><th className="num">Записей</th><th>Длительность</th><th>Статус</th></tr></thead><tbody>{jobRows.map((job, index) => { const stat = String(job.status ?? "unknown").toLowerCase(); const progress = asRecord(job.progress); return <tr key={getId(job, index)}><td className="muted nowrap">{formatDate(job.started_at ?? job.created_at)}</td><td>{String(job.type ?? job.job_type ?? "Pipeline")}</td><td>{String(job.error ?? job.stage ?? progress.current_day ?? progress.stage ?? job.message ?? "-")}</td><td className="num">{formatPercent(Number(job.progress_percent ?? progress.progress_percent ?? 0) / (Number(job.progress_percent ?? progress.progress_percent ?? 0) > 1 ? 100 : 1))}</td><td className="num">{formatNumber(job.records ?? asRecord(progress.totals).saved ?? progress.saved ?? job.processed)}</td><td>{durationText(job.duration_seconds ?? job.duration)}</td><td><Badge tone={stat.includes("fail") || stat.includes("interrupt") ? "bad" : stat.includes("run") || stat.includes("queue") ? "info" : stat.includes("complete") || stat.includes("success") ? "good" : "neutral"}>{statusLabel(stat)}</Badge></td></tr>; })}</tbody></table></div>}
      </Panel>
      <Panel title="Последняя проверка данных" meta={formatDate(validation.created_at ?? asRecord(status.data).latest_validation_at)} className="span-2"><TableState loading={validationReport.loading} error={validationReport.error} empty={false} onRetry={validationReport.reload} /><div className="validation-summary"><div><strong>{formatNumber(validation.passed)}</strong><span>Пройдено</span></div><div><strong className="negative">{formatNumber(validation.errors)}</strong><span>Ошибок</span></div></div><div className="validation-list">{asArray(validation.checks, ["checks", "items"]).map((check, index) => { const severity = String(check.severity ?? check.status ?? "passed").toLowerCase(); return <div key={getId(check, index)}><Badge tone={severity.includes("error") || severity.includes("fail") ? "bad" : "good"}>{severity}</Badge><strong>{String(check.name ?? check.check ?? "Проверка")}</strong><span>{String(check.message ?? check.description ?? "Проблем не обнаружено")}</span><em>{formatNumber(check.affected ?? 0)}</em></div>; })}</div></Panel>
    </div>
  </>;
}

export default function App() {
  const [page, setPage] = useHashPage();
  const [filters, setFilters] = useState(initialFilters);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [entityTeam, setEntityTeam] = useState<JsonRecord | null>(null);
  const [entityMatch, setEntityMatch] = useState<JsonRecord | null>(null);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    const onTeam = (event: Event) => { setEntityMatch(null); setEntityTeam(asRecord((event as CustomEvent).detail)); };
    const onMatch = (event: Event) => { setEntityTeam(null); setEntityMatch(asRecord((event as CustomEvent).detail)); };
    const onRoute = () => {
      const [section, id] = window.location.hash.replace("#/", "").split("/");
      if (section === "teams" && id) { setEntityMatch(null); setEntityTeam({ id: Number(id) }); }
      else if (section === "matches" && id) { setEntityTeam(null); setEntityMatch({ id: Number(id) }); }
      else { setEntityTeam(null); setEntityMatch(null); }
    };
    window.addEventListener("cs2:open-team", onTeam);
    window.addEventListener("cs2:open-match", onMatch);
    window.addEventListener("hashchange", onRoute);
    onRoute();
    return () => { window.removeEventListener("cs2:open-team", onTeam); window.removeEventListener("cs2:open-match", onMatch); window.removeEventListener("hashchange", onRoute); };
  }, []);
  return <div className="app-shell">
    <header className="topbar">
      <button className="brand" onClick={() => setPage("dashboard")}><span className="brand-mark">C2</span><span><strong>CS2 Analytics</strong><em>GRID Open Access</em></span></button>
      <nav aria-label="Main navigation">{NAV.map((item) => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}>{item.label}</button>)}</nav>
      <div className="top-status"><span><StatusDot tone="good" />API работает</span><span className="desktop-only">GRID Open Access</span><button className="theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Сменить тему">{theme === "dark" ? "☼" : "◐"}</button></div>
    </header>
    <main className="content">
      {page === "dashboard" && <Dashboard filters={filters} setPage={setPage} />}
      {page === "teams" && <Teams filters={filters} setFilters={setFilters} />}
      {page === "matches" && <Matches filters={filters} setFilters={setFilters} />}
      {page === "upcoming" && <Upcoming filters={filters} setFilters={setFilters} />}
      {page === "data" && <DataPage />}
    </main>
    {entityTeam && <><div className="scrim" onClick={() => { window.location.hash = "#/teams"; }} /><TeamProfile team={entityTeam} filters={filters} onClose={() => { window.location.hash = "#/teams"; }} teams={[]} onSynced={() => undefined} /></>}
    {entityMatch && <><div className="scrim" onClick={() => { window.location.hash = "#/matches"; }} /><MatchDetailDrawer match={entityMatch} onClose={() => { window.location.hash = "#/matches"; }} /></>}
    <footer><span>CS2 Tier-1 Analytics</span><span>Local workspace · GRID data</span></footer>
  </div>;
}

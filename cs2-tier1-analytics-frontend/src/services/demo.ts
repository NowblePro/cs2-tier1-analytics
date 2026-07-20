const now = new Date();
const ago = (hours: number) => new Date(now.getTime() - hours * 3600000).toISOString();
const future = (hours: number) => new Date(now.getTime() + hours * 3600000).toISOString();

export const demo = {
  summary: { teams: 30, players: 182, matches: 4821, maps: 13906, player_stats: 28411, grid_raw: 96402, grid_ids: 4876, grid_stats: 18220 },
  status: { cursor: "2026-07-20T12:00Z", last_sync: ago(0.15), latest_match: ago(4), latest_raw_fetch: ago(0.2), latest_stats_fetch: ago(2), validation_status: "passed", latest_validation_report: ago(3) },
  teams: [
    { id: "vitality", name: "Vitality", country: "France", matches: 42, series_win_rate: .71, map_win_rate: .64, round_diff: 5.8, pistol_win_rate: .56, grid_rating: 1.14, form: "WWLWW", updated_at: ago(2) },
    { id: "spirit", name: "Team Spirit", country: "Europe", matches: 39, series_win_rate: .69, map_win_rate: .62, round_diff: 4.9, pistol_win_rate: .54, grid_rating: 1.12, form: "WWWLW", updated_at: ago(3) },
    { id: "mouz", name: "MOUZ", country: "Europe", matches: 44, series_win_rate: .64, map_win_rate: .59, round_diff: 3.2, pistol_win_rate: .52, grid_rating: 1.08, form: "WLWWW", updated_at: ago(4) },
    { id: "navi", name: "Natus Vincere", country: "Europe", matches: 41, series_win_rate: .61, map_win_rate: .57, round_diff: 2.5, pistol_win_rate: .51, grid_rating: 1.06, form: "LWWLW", updated_at: ago(5) },
    { id: "falcons", name: "Falcons", country: "Europe", matches: 38, series_win_rate: .58, map_win_rate: .55, round_diff: 1.7, pistol_win_rate: .50, grid_rating: 1.04, form: "WWLLW", updated_at: ago(6) },
    { id: "g2", name: "G2 Esports", country: "Europe", matches: 46, series_win_rate: .55, map_win_rate: .53, round_diff: .8, pistol_win_rate: .48, grid_rating: 1.02, form: "LWLWW", updated_at: ago(8) },
  ],
  matches: [
    { id: "m1", start_time: ago(4), event_name: "IEM Cologne 2026", team1: { name: "Vitality" }, team2: { name: "MOUZ" }, team1_score: 2, team2_score: 1, format: "BO3", status: "completed" },
    { id: "m2", start_time: ago(8), event_name: "IEM Cologne 2026", team1: { name: "Team Spirit" }, team2: { name: "Natus Vincere" }, team1_score: 2, team2_score: 0, format: "BO3", status: "completed" },
    { id: "m3", start_time: ago(20), event_name: "BLAST Open", team1: { name: "Falcons" }, team2: { name: "G2 Esports" }, team1_score: 1, team2_score: 2, format: "BO3", status: "completed" },
    { id: "m4", start_time: ago(30), event_name: "BLAST Open", team1: { name: "MOUZ" }, team2: { name: "Team Spirit" }, team1_score: 0, team2_score: 2, format: "BO3", status: "completed" },
  ],
  upcoming: [
    { id: "u1", start_time: future(3), event_name: "IEM Cologne 2026", team1: { name: "Vitality", form: "WWLWW" }, team2: { name: "Team Spirit", form: "WWWLW" }, format: "BO3", status: "scheduled" },
    { id: "u2", start_time: future(7), event_name: "IEM Cologne 2026", team1: { name: "MOUZ" }, team2: { name: "Natus Vincere" }, format: "BO3", status: "scheduled" },
    { id: "u3", start_time: future(26), event_name: "BLAST Open", team1: { name: "G2 Esports" }, team2: { name: "Falcons" }, format: "BO3", status: "scheduled" },
  ],
  jobs: [
    { id: "j1", started_at: ago(.1), type: "GRID sync", stage: "Fetch stats · page 18/42", status: "running", progress_percent: 43, records: 1842, duration: "6m 12s" },
    { id: "j2", started_at: ago(3), type: "Validate", stage: "12 checks passed", status: "completed", progress_percent: 100, records: 4821, duration: "22s" },
    { id: "j3", started_at: ago(24), type: "Metrics compute", stage: "Team metrics", status: "completed", progress_percent: 100, records: 30, duration: "1m 08s" },
  ],
  players: [
    { id: "p1", name: "apEX", rating: 1.02, kd: .98 }, { id: "p2", name: "ZywOo", rating: 1.31, kd: 1.42 }, { id: "p3", name: "flameZ", rating: 1.14, kd: 1.16 }, { id: "p4", name: "ropz", rating: 1.18, kd: 1.24 }, { id: "p5", name: "mezii", rating: 1.09, kd: 1.08 },
  ],
  maps: [{ name: "Mirage", played: 24, win_rate: .67 }, { name: "Dust2", played: 18, win_rate: .61 }, { name: "Nuke", played: 21, win_rate: .57 }, { name: "Inferno", played: 16, win_rate: .50 }],
  estimate: { windows: 42, estimated_requests: 840, estimated_matches: 620, coverage: .81, eta: "18–26 min" },
  preview: { team1: { name: "Vitality", form: "WWLWW" }, team2: { name: "Team Spirit", form: "WWWLW" }, edge_score: 16, confidence: "medium" },
  comparison: [
    { label: "Series win rate", a: .71, b: .69, display1: "71%", display2: "69%" },
    { label: "Map win rate", a: .64, b: .62, display1: "64%", display2: "62%" },
    { label: "Round differential", a: 5.8, b: 4.9, display1: "+5.8", display2: "+4.9" },
    { label: "Pistol win rate", a: .56, b: .54, display1: "56%", display2: "54%" },
    { label: "GRID rating", a: 1.14, b: 1.12 },
  ],
  mapComparison: [
    { name: "Mirage", a: .67, b: .58, sample_a: 24, sample_b: 19 }, { name: "Dust2", a: .61, b: .65, sample_a: 18, sample_b: 20 }, { name: "Nuke", a: .57, b: .63, sample_a: 21, sample_b: 27 }, { name: "Inferno", a: .50, b: .54, sample_a: 16, sample_b: 13 },
  ],
  validation: [
    { name: "Matches have both team IDs", status: "passed", affected: 0 }, { name: "Player stats freshness", status: "warning", message: "27 rows older than 48 hours", affected: 27 }, { name: "Duplicate GRID payloads", status: "passed", affected: 0 },
  ],
};

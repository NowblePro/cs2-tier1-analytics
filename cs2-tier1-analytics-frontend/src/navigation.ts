import { asRecord } from "./services/api";


export function openTeamCard(value: unknown) {
  const team = asRecord(value);
  if (team.id == null) return;
  window.location.hash = `#/teams/${String(team.id)}`;
  window.dispatchEvent(new CustomEvent("cs2:open-team", { detail: team }));
}


export function openMatchCard(value: unknown) {
  const match = asRecord(value);
  if (match.id == null) return;
  window.location.hash = `#/matches/${String(match.id)}`;
  window.dispatchEvent(new CustomEvent("cs2:open-match", { detail: match }));
}

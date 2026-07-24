import { completenessLabel, statusLabel } from "../labels";
import { openTeamCard } from "../navigation";
import {
  asArray,
  asRecord,
  formatDate,
  getId,
  getName,
  type JsonRecord,
} from "../services/api";
import { Badge, TeamMark } from "./ui";


export function MatchesTable({
  rows,
  onOpen,
}: {
  rows: JsonRecord[];
  onOpen?: (row: JsonRecord) => void;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Дата</th><th>Турнир</th><th>Команда A</th>
            <th className="num">Счёт</th><th>Команда B</th>
            <th>Карты и счёт</th><th>Формат</th><th>Статус</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const a = getName(row.team1 ?? row.team_a ?? row.home_team, "TBD");
            const b = getName(row.team2 ?? row.team_b ?? row.away_team, "TBD");
            const scoreA = row.team1_score ?? row.score_a ?? row.home_score;
            const scoreB = row.team2_score ?? row.score_b ?? row.away_score;
            const status = String(row.status ?? "completed");
            const completeness = asRecord(row.completeness);
            const flags = asRecord(completeness.flags);
            const detailed = Boolean(flags.maps || flags.players || flags.rounds);
            const mapRows = asArray(row.maps, ["maps", "items"]);
            const eventPriority = asRecord(row.event_priority);
            const tier = String(eventPriority.tier ?? "other");
            const tierTone = tier === "tier-1" ? "good" as const : tier === "tier-2" ? "info" as const : tier === "unknown" ? "neutral" as const : "warn" as const;
            return (
              <tr
                key={getId(row, index)}
                onClick={() => onOpen?.(row)}
                className={`${onOpen ? "clickable" : ""} ${detailed ? "detailed-row" : ""}`}
              >
                <td className="muted nowrap">{formatDate(row.start_time ?? row.date ?? row.scheduled_at)}</td>
                <td><span className="event-cell"><strong>{String(row.event_name ?? row.event ?? "—")}</strong><Badge tone={tierTone}>{String(eventPriority.label ?? "Other")}</Badge></span></td>
                <td>
                  <button
                    className="entity-link team-cell"
                    onClick={(event) => {
                      event.stopPropagation();
                      openTeamCard(row.team1 ?? row.team_a ?? row.home_team);
                    }}
                  >
                    <TeamMark name={a} />{a}
                  </button>
                </td>
                <td className="num score">{scoreA != null ? `${scoreA} : ${scoreB ?? 0}` : "—"}</td>
                <td>
                  <button
                    className="entity-link team-cell"
                    onClick={(event) => {
                      event.stopPropagation();
                      openTeamCard(row.team2 ?? row.team_b ?? row.away_team);
                    }}
                  >
                    <TeamMark name={b} />{b}
                  </button>
                </td>
                <td>
                  <div className="map-score-list">
                    {mapRows.length
                      ? mapRows.map((map, mapIndex) => (
                          <span key={getId(map, mapIndex)}>
                            <strong>{String(map.name ?? map.map_name ?? "?")}</strong>{" "}
                            {String(map.score_team1 ?? "-")}:{String(map.score_team2 ?? "-")}
                          </span>
                        ))
                      : <span className="muted">Только результат серии</span>}
                  </div>
                </td>
                <td className="muted">{String(row.format ?? row.best_of ?? "—")}</td>
                <td>
                  <Badge tone={status.includes("live") ? "bad" : status.includes("scheduled") || status.includes("upcoming") ? "info" : "neutral"}>
                    {statusLabel(status)}
                  </Badge>{" "}
                  <Badge tone={String(completeness.level) === "rounds" ? "good" : "neutral"}>
                    {completenessLabel(completeness.level)}
                  </Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

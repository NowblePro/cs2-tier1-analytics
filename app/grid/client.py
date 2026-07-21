from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class GridApiError(RuntimeError):
    pass


@dataclass
class GridSeriesSummary:
    id: str
    start_time_scheduled: str | None
    tournament_name: str | None
    title_name: str | None
    teams: list[dict[str, Any]]
    title_id: str | None = None
    workflow_status: str | None = None


class GridClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        api_key = (self.settings.grid_api_key or "").strip()
        if not api_key:
            raise GridApiError("GRID_API_KEY is not set")
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise GridApiError("GRID_API_KEY contains non-ASCII characters. Set it again in quotes in PowerShell.") from exc
        self.central_url = f"{self.settings.grid_base_url.rstrip('/')}/central-data/graphql"
        self.series_state_url = f"{self.settings.grid_base_url.rstrip('/')}/live-data-feed/series-state/graphql"
        self.stats_url = f"{self.settings.grid_base_url.rstrip('/')}/statistics-feed/graphql"
        limit = max(1, self.settings.grid_request_limit_per_minute)
        stats_limit = max(1, min(self.settings.grid_stats_request_limit_per_minute, 9))
        self._default_min_request_interval = 60.0 / limit
        self._stats_min_request_interval = 60.0 / stats_limit
        self._last_request_at_by_url: dict[str, float] = {}
        self._headers = {
            "x-api-key": api_key,
            "content-type": "application/json",
            "user-agent": "cs2-tier1-analytics/0.1",
        }
        self._client = self._new_http_client()

    def _new_http_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.settings.request_timeout, headers=self._headers)

    def close(self) -> None:
        self._client.close()

    def _post(self, url: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        min_interval = self._stats_min_request_interval if url == self.stats_url else self._default_min_request_interval
        response = None
        last_error: httpx.RequestError | None = None
        for attempt in range(max(1, self.settings.max_retries)):
            elapsed = time.monotonic() - self._last_request_at_by_url.get(url, 0.0)
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            try:
                response = self._client.post(url, json={"query": query, "variables": variables})
                break
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning("GRID API request failed on attempt %s/%s: %s", attempt + 1, self.settings.max_retries, exc)
                self._last_request_at_by_url[url] = time.monotonic()
                self._client.close()
                self._client = self._new_http_client()
                if attempt + 1 < self.settings.max_retries:
                    time.sleep(min(10.0, 1.5 * (attempt + 1)))
        if response is None:
            raise GridApiError(f"GRID API request failed: {last_error}") from last_error
        self._last_request_at_by_url[url] = time.monotonic()
        if response.status_code >= 400:
            raise GridApiError(f"GRID API returned HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if payload.get("errors"):
            raise GridApiError(f"GRID API GraphQL errors: {payload['errors']}")
        return payload["data"]

    def list_series(
        self,
        date_from: datetime,
        date_to: datetime,
        first: int = 50,
        after: str | None = None,
        order_direction: str = "ASC",
    ) -> tuple[list[GridSeriesSummary], dict[str, Any]]:
        query = """
        query AllSeries($gte: String!, $lte: String!, $first: Int!, $after: String, $direction: OrderDirection!) {
          allSeries(
            first: $first
            after: $after
            filter: { startTimeScheduled: { gte: $gte, lte: $lte } }
            orderBy: StartTimeScheduled
            orderDirection: $direction
          ) {
            edges {
              node {
                id
                startTimeScheduled
                workflowStatus
                title { id name }
                teams { baseInfo { id name } }
                tournament { id name }
              }
            }
            pageInfo { endCursor hasNextPage }
          }
        }
        """
        data = self._post(
            self.central_url,
            query,
            {
                "gte": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lte": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "first": first,
                "after": after,
                "direction": order_direction,
            },
        )
        series = data["allSeries"]
        rows = [
            GridSeriesSummary(
                id=edge["node"]["id"],
                start_time_scheduled=edge["node"].get("startTimeScheduled"),
                tournament_name=(edge["node"].get("tournament") or {}).get("name"),
                title_name=(edge["node"].get("title") or {}).get("name"),
                teams=edge["node"].get("teams") or [],
                title_id=(edge["node"].get("title") or {}).get("id"),
                workflow_status=edge["node"].get("workflowStatus"),
            )
            for edge in series["edges"]
        ]
        return rows, series["pageInfo"]

    def schema_type_info(self, endpoint: str, type_name: str) -> dict[str, Any] | None:
        url = self._endpoint_url(endpoint)
        query = """
        query TypeInfo($name: String!) {
          __type(name: $name) {
            name
            kind
            fields {
              name
              args { name type { kind name ofType { kind name ofType { kind name } } } }
              type { kind name ofType { kind name ofType { kind name } } }
            }
            inputFields {
              name
              defaultValue
              type { kind name ofType { kind name ofType { kind name } } }
            }
            enumValues { name }
          }
        }
        """
        return self._post(url, query, {"name": type_name}).get("__type")

    def series_state(self, series_id: str) -> dict[str, Any]:
        query = """
        query SeriesState($id: ID!) {
          seriesState(id: $id) {
            id
            startedAt
            started
            finished
            teams {
              id
              name
              won
              score
              kills
              deaths
            }
            games {
              id
              sequenceNumber
              started
              finished
              map {
                id
                name
              }
              teams {
                id
                name
                won
                score
                kills
                deaths
                players {
                  id
                  name
                  kills
                  deaths
                  killAssistsGiven
                  killAssistsReceived
                  ... on GamePlayerStateCs2 {
                    damageDealt
                    damageTaken
                    headshots
                  }
                }
                ... on GameTeamStateCs2 {
                  side
                  damageDealt
                  damageTaken
                  headshots
                }
              }
            }
          }
        }
        """
        try:
            data = self._post(self.series_state_url, query, {"id": series_id})
        except GridApiError as exc:
            logger.warning("Falling back to minimal GRID seriesState query for %s: %s", series_id, exc)
            data = self._post(self.series_state_url, self._minimal_series_state_query(), {"id": series_id})
        if "seriesState" not in data:
            keys = ", ".join(data.keys()) or "<empty>"
            raise GridApiError(f"GRID seriesState response for {series_id} did not include seriesState; data keys: {keys}")
        state = data["seriesState"]
        if state is None:
            raise GridApiError(f"GRID seriesState response for {series_id} is empty")
        return state

    def team_statistics(self, team_id: str, window_name: str = "LAST_MONTH") -> dict[str, Any]:
        query = """
        query TeamStats($teamId: ID!, $filter: TeamStatisticsFilter!) {
          teamStatistics(teamId: $teamId, filter: $filter) {
            id
            aggregationSeriesIds
            series {
              ... on Cs2TeamSeriesStatistics {
                count
                kills { sum avg }
                deaths { sum avg }
                score { sum avg }
                won { value count percentage }
                duration { avg }
                headshots { sum avg }
              }
            }
            game {
              ... on TeamGameStatisticsCs2 {
                count
                kills { sum avg }
                deaths { sum avg }
                score { sum avg }
                won { value count percentage }
                damageDealt { sum avg }
                damageTaken { sum avg }
              }
            }
            segment {
              ... on TeamSegmentStatisticsCs2 {
                type
                count
                won { value count percentage }
                kills { sum avg }
                deaths { sum avg }
                firstKill { value count percentage }
                wonFirst { value count percentage }
                damageDealt { sum avg }
              }
            }
          }
        }
        """
        data = self._post(self.stats_url, query, {"teamId": str(team_id), "filter": {"timeWindow": window_name}})
        return data["teamStatistics"]

    def player_statistics(self, player_id: str, window_name: str = "LAST_MONTH") -> dict[str, Any]:
        query = """
        query PlayerStats($playerId: ID!, $filter: PlayerStatisticsFilter!) {
          playerStatistics(playerId: $playerId, filter: $filter) {
            id
            aggregationSeriesIds
            series {
              ... on Cs2PlayerSeriesStatistics {
                count
                kills { sum avg }
                deaths { sum avg }
                won { value count percentage }
                firstKill { value count percentage }
                headshots { sum avg }
              }
            }
            game {
              ... on PlayerGameStatisticsCs2 {
                count
                kills { sum avg }
                deaths { sum avg }
                won { value count percentage }
                damageDealt { sum avg }
                damageTaken { sum avg }
              }
            }
          }
        }
        """
        data = self._post(self.stats_url, query, {"playerId": str(player_id), "filter": {"timeWindow": window_name}})
        return data["playerStatistics"]

    @staticmethod
    def _minimal_series_state_query() -> str:
        return """
        query SeriesState($id: ID!) {
          seriesState(id: $id) {
            id
            startedAt
            started
            finished
            teams {
              won
              score
              kills
              deaths
            }
            games {
              id
              sequenceNumber
              started
              finished
              teams {
                won
                score
                kills
                deaths
              }
            }
          }
        }
        """

    def query_type_fields(self, endpoint: str, type_name: str = "Query") -> list[dict[str, Any]]:
        url = self._endpoint_url(endpoint)
        query = """
        query TypeFields($name: String!) {
          __type(name: $name) {
            name
            kind
            fields {
              name
              args { name type { kind name ofType { kind name ofType { kind name } } } }
              type { kind name ofType { kind name ofType { kind name } } }
            }
            inputFields {
              name
              defaultValue
              type { kind name ofType { kind name ofType { kind name } } }
            }
            enumValues {
              name
            }
          }
        }
        """
        type_info = self.schema_type_info(endpoint, type_name)
        if not type_info:
            return []
        return type_info.get("fields") or type_info.get("inputFields") or type_info.get("enumValues") or []

    def schema_type_names(self, endpoint: str, contains: str | None = None) -> list[str]:
        url = self._endpoint_url(endpoint)
        query = """
        query SchemaTypes {
          __schema { types { name kind } }
        }
        """
        data = self._post(url, query, {})
        names = sorted(t["name"] for t in data["__schema"]["types"] if not t["name"].startswith("__"))
        if contains:
            needle = contains.lower()
            names = [name for name in names if needle in name.lower()]
        return names

    def schema_report(self, endpoint: str, keywords: list[str], types_per_keyword: int) -> dict[str, Any]:
        url = self._endpoint_url(endpoint)
        query = """
        query SchemaReport {
          __schema {
            queryType { name }
            types {
              name
              kind
              fields {
                name
                args { name type { kind name ofType { kind name ofType { kind name } } } }
                type { kind name ofType { kind name ofType { kind name } } }
              }
            }
          }
        }
        """
        schema = self._post(url, query, {})["__schema"]
        types = {t["name"]: t for t in schema["types"] if not t["name"].startswith("__")}
        query_type_name = schema["queryType"]["name"]
        selected_names: list[str] = []
        types_by_keyword: dict[str, list[str]] = {}
        for keyword in keywords:
            names = sorted([name for name in types if keyword.lower() in name.lower()])[:types_per_keyword]
            types_by_keyword[keyword] = names
            selected_names.extend(names)
        selected_names.append(query_type_name)
        selected_names = sorted(set(selected_names))
        return {
            "query_type": query_type_name,
            "query_fields": types.get(query_type_name, {}).get("fields") or [],
            "types_by_keyword": types_by_keyword,
            "type_fields": {name: types.get(name, {}).get("fields") or [] for name in selected_names},
        }

    def _endpoint_url(self, endpoint: str) -> str:
        if endpoint == "central":
            return self.central_url
        if endpoint == "state":
            return self.series_state_url
        if endpoint == "stats":
            return self.stats_url
        raise ValueError(f"Unknown GRID endpoint: {endpoint}")

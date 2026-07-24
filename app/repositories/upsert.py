from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schema import Event, Match, MatchMap, Player, PlayerMapStat, RankingSnapshot, RankingSnapshotTeam, Round, Team
from app.scraping.dto import MatchDTO, RankingSnapshotDTO, TeamDTO


class AnalyticsRepository:
    def __init__(self, session: Session):
        self.session = session
        self.new_matches = 0
        self.updated_matches = 0

    def upsert_team(self, dto: TeamDTO) -> Team:
        team = self.session.scalar(select(Team).where(Team.hltv_team_id == dto.hltv_team_id))
        if team is None:
            team = Team(hltv_team_id=dto.hltv_team_id, name=dto.name, country=dto.country)
            self.session.add(team)
            self.session.flush()
        else:
            team.name = dto.name
            team.country = dto.country or team.country
        return team

    def save_ranking_snapshot(self, dto: RankingSnapshotDTO) -> RankingSnapshot:
        snapshot = RankingSnapshot(ranking_date=dto.ranking_date, source_url=dto.source_url)
        self.session.add(snapshot)
        self.session.flush()
        for ranked in dto.teams:
            team = self.upsert_team(TeamDTO(hltv_team_id=ranked.hltv_team_id, name=ranked.name, country=ranked.country))
            team.logo_url = ranked.logo_url or team.logo_url
            self.session.add(RankingSnapshotTeam(snapshot_id=snapshot.id, team_id=team.id, rank=ranked.rank, points=ranked.points))
        return snapshot

    def save_match(self, dto: MatchDTO) -> Match:
        team1 = self.upsert_team(dto.team1) if dto.team1 else None
        team2 = self.upsert_team(dto.team2) if dto.team2 else None
        winner = None
        if dto.winner_hltv_team_id:
            winner = self.session.scalar(select(Team).where(Team.hltv_team_id == dto.winner_hltv_team_id))
        event = None
        if dto.event_name:
            event = self.session.scalar(select(Event).where(Event.name == dto.event_name))
            if event is None:
                event = Event(name=dto.event_name)
                self.session.add(event)
                self.session.flush()
        match = self.session.scalar(select(Match).where(Match.hltv_match_id == dto.hltv_match_id))
        if match is None:
            match = Match(hltv_match_id=dto.hltv_match_id, source_url=dto.source_url)
            self.session.add(match)
            self.new_matches += 1
        else:
            self.updated_matches += 1
        match.match_time = dto.match_time
        match.status = dto.status
        match.team1_id = team1.id if team1 else None
        match.team2_id = team2.id if team2 else None
        match.winner_team_id = winner.id if winner else None
        match.event_id = event.id if event else None
        match.best_of = dto.best_of
        match.score_team1 = dto.score_team1
        match.score_team2 = dto.score_team2
        match.source_url = dto.source_url
        self.session.flush()

        maps_by_number: dict[int, MatchMap] = {
            item.map_number: item for item in self.session.scalars(select(MatchMap).where(MatchMap.match_id == match.id)).all()
        }
        for map_dto in dto.maps:
            match_map = maps_by_number.get(map_dto.map_number)
            if match_map is None:
                match_map = MatchMap(match_id=match.id, map_number=map_dto.map_number, name=map_dto.name)
                self.session.add(match_map)
            map_winner = self.session.scalar(select(Team).where(Team.hltv_team_id == map_dto.winner_hltv_team_id)) if map_dto.winner_hltv_team_id else None
            match_map.hltv_mapstats_id = map_dto.hltv_mapstats_id
            match_map.name = map_dto.name
            match_map.winner_team_id = map_winner.id if map_winner else None
            match_map.score_team1 = map_dto.score_team1
            match_map.score_team2 = map_dto.score_team2
            match_map.first_half_team1 = map_dto.first_half_team1
            match_map.first_half_team2 = map_dto.first_half_team2
            match_map.second_half_team1 = map_dto.second_half_team1
            match_map.second_half_team2 = map_dto.second_half_team2
            match_map.overtime = map_dto.overtime
            self.session.flush()
            if any(round_dto.map_number == match_map.map_number for round_dto in dto.rounds):
                self._replace_rounds(match_map, dto)
            self._upsert_player_stats(match_map, dto)
        return match

    def _replace_rounds(self, match_map: MatchMap, dto: MatchDTO) -> None:
        self.session.query(Round).filter(Round.match_map_id == match_map.id).delete()
        for round_dto in [r for r in dto.rounds if r.map_number == match_map.map_number]:
            winner = self.session.scalar(select(Team).where(Team.hltv_team_id == round_dto.winner_hltv_team_id)) if round_dto.winner_hltv_team_id else None
            self.session.add(
                Round(
                    match_map_id=match_map.id,
                    round_number=round_dto.round_number,
                    half_number=round_dto.half_number,
                    is_overtime=round_dto.is_overtime,
                    winner_team_id=winner.id if winner else None,
                    winner_side=round_dto.winner_side,
                    end_method=round_dto.end_method,
                    score_team1_after=round_dto.score_team1_after,
                    score_team2_after=round_dto.score_team2_after,
                    is_pistol=round_dto.is_pistol,
                )
            )

    def _upsert_player_stats(self, match_map: MatchMap, dto: MatchDTO) -> None:
        for stat in [s for s in dto.player_stats if s.map_number == match_map.map_number]:
            team = self.session.scalar(select(Team).where(Team.hltv_team_id == stat.hltv_team_id)) if stat.hltv_team_id else None
            player = self.session.scalar(select(Player).where(Player.hltv_player_id == stat.hltv_player_id))
            if player is None:
                player = Player(hltv_player_id=stat.hltv_player_id, nickname=stat.nickname, current_team_id=team.id if team else None)
                self.session.add(player)
                self.session.flush()
            player.nickname = stat.nickname
            player.current_team_id = team.id if team else player.current_team_id
            row = self.session.scalar(select(PlayerMapStat).where(PlayerMapStat.match_map_id == match_map.id, PlayerMapStat.player_id == player.id))
            if row is None:
                row = PlayerMapStat(match_map_id=match_map.id, player_id=player.id)
                self.session.add(row)
            row.team_id = team.id if team else None
            row.kills = stat.kills
            row.deaths = stat.deaths
            row.assists = stat.assists
            row.kd_diff = stat.kd_diff
            row.kd_ratio = stat.kd_ratio
            row.adr = stat.adr
            row.kast = stat.kast
            row.rating = stat.rating
            row.headshot_percentage = stat.headshot_percentage

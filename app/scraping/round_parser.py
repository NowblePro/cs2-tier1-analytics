from app.scraping.dto import RoundDTO
from app.scraping.parsing_utils import attr_int, clean_text, soup


def is_pistol_round(round_number: int, is_overtime: bool) -> bool:
    return not is_overtime and round_number in {1, 13}


def parse_rounds(html: str) -> list[RoundDTO]:
    doc = soup(html)
    rounds: list[RoundDTO] = []
    for row in doc.select("[data-round]"):
        round_number = attr_int(row, "data-round")
        map_number = attr_int(row, "data-map-number") or 1
        if round_number is None:
            continue
        overtime = (row.get("data-overtime") or "").lower() == "true"
        half_number = attr_int(row, "data-half")
        winner_team_id = attr_int(row, "data-winner-team-id")
        score = row.get("data-score", "")
        score_team1_after = score_team2_after = None
        if "-" in score:
            left, right = score.split("-", 1)
            score_team1_after = int(left)
            score_team2_after = int(right)
        rounds.append(
            RoundDTO(
                map_number=map_number,
                round_number=round_number,
                half_number=half_number,
                is_overtime=overtime,
                winner_hltv_team_id=winner_team_id,
                winner_side=clean_text(row.get("data-side")).upper() or None,
                end_method=clean_text(row.get("data-method")) or None,
                score_team1_after=score_team1_after,
                score_team2_after=score_team2_after,
                is_pistol=is_pistol_round(round_number, overtime),
            )
        )
    return rounds


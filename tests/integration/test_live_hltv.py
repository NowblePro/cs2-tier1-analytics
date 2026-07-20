import os

import pytest

from app.config import get_settings
from app.scraping.client import HltvClient
from app.scraping.ranking_parser import parse_ranking


@pytest.mark.live
def test_live_hltv_ranking_fetch():
    if os.getenv("RUN_HLTV_LIVE") != "1":
        pytest.skip("Set RUN_HLTV_LIVE=1 to run live HLTV test")
    client = HltvClient(get_settings(), force_refresh=True)
    try:
        result = client.fetch("/ranking/teams")
    finally:
        client.close()
    ranking = parse_ranking(result.html, result.url)
    assert ranking.teams

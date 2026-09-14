from unittest.mock import Mock

import pandas as pd
import pytest

from src.ingestion.odds_feed import fetch_live_odds

SAMPLE_EVENT = {
    "commence_time": "2026-09-20T14:00:00Z",
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "bookmakers": [
        {
            "key": "pinnacle",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 2.20},
                {"name": "Draw", "price": 3.40},
                {"name": "Chelsea", "price": 3.10},
            ]}],
        },
        {
            "key": "bet365",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 2.10},
                {"name": "Draw", "price": 3.60},
                {"name": "Chelsea", "price": 3.20},
            ]}],
        },
        {
            # A book with only an unrelated market — should be ignored,
            # not crash the aggregation.
            "key": "oddball",
            "markets": [{"key": "totals", "outcomes": [{"name": "Over 2.5", "price": 1.9}]}],
        },
    ],
}


def _session_returning(payload):
    session = Mock()
    session.get.return_value = Mock(json=Mock(return_value=payload), raise_for_status=Mock())
    return session


class TestFetchLiveOdds:
    def test_averages_across_all_bookmakers(self):
        session = _session_returning([SAMPLE_EVENT])
        df = fetch_live_odds("E0", api_key="test-key", session=session)

        assert len(df) == 1
        row = df.iloc[0]
        assert row["odds_home"] == pytest.approx((2.20 + 2.10) / 2)
        assert row["odds_draw"] == pytest.approx((3.40 + 3.60) / 2)
        assert row["odds_away"] == pytest.approx((3.10 + 3.20) / 2)
        assert row["n_bookmakers"] == 3  # counts all bookmakers on the event, including the h2h-less one

    def test_missing_api_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        df = fetch_live_odds("E0")
        assert df.empty

    def test_unmapped_league_returns_empty(self):
        df = fetch_live_odds("XX", api_key="test-key")
        assert df.empty

    def test_event_with_no_h2h_market_is_skipped(self):
        event = dict(SAMPLE_EVENT, bookmakers=[SAMPLE_EVENT["bookmakers"][2]])  # only the odds-less book
        session = _session_returning([event])
        df = fetch_live_odds("E0", api_key="test-key", session=session)
        assert df.empty

    def test_network_failure_returns_empty_not_raise(self):
        session = Mock()
        session.get.side_effect = ConnectionError("blocked")
        df = fetch_live_odds("E0", api_key="test-key", session=session)
        assert df.empty

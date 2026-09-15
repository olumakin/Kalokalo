from unittest.mock import Mock

import pandas as pd
import pytest
import requests

import src.ingestion.odds_feed as odds_feed_mod
from src.ingestion.odds_feed import (
    FIXTURE_SOURCE_FREE_SCHEDULE,
    FIXTURE_SOURCE_LIVE_ODDS,
    FIXTURE_SOURCE_SAMPLE_CARD,
    fetch_free_schedule,
    fetch_live_odds,
    get_upcoming_fixtures,
)

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
        df = fetch_live_odds("E0", api_key="test-key-1234567890abcdef", session=session)

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

    def test_malformed_short_key_skips_the_request_entirely(self):
        session = Mock()
        df = fetch_live_odds("E0", api_key="short", session=session)
        assert df.empty
        session.get.assert_not_called()  # never wastes a round trip on an obviously-bad key

    def test_unmapped_league_returns_empty(self):
        df = fetch_live_odds("XX", api_key="test-key-1234567890abcdef")
        assert df.empty

    def test_event_with_no_h2h_market_is_skipped(self):
        event = dict(SAMPLE_EVENT, bookmakers=[SAMPLE_EVENT["bookmakers"][2]])  # only the odds-less book
        session = _session_returning([event])
        df = fetch_live_odds("E0", api_key="test-key-1234567890abcdef", session=session)
        assert df.empty

    def test_network_failure_returns_empty_not_raise(self):
        session = Mock()
        session.get.side_effect = ConnectionError("blocked")
        df = fetch_live_odds("E0", api_key="test-key-1234567890abcdef", session=session)
        assert df.empty

    def test_invalid_key_401_returns_empty_not_raise(self):
        session = Mock()
        resp = Mock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        session.get.return_value = resp
        df = fetch_live_odds("E0", api_key="bad-key-but-long-enough-1234", session=session)
        assert df.empty

    def test_rate_limited_429_returns_empty_not_raise(self):
        session = Mock()
        resp = Mock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("429 Too Many Requests")
        session.get.return_value = resp
        df = fetch_live_odds("E0", api_key="test-key-1234567890abcdef", session=session)
        assert df.empty


@pytest.fixture
def fallback_csv(tmp_path):
    path = tmp_path / "upcoming.csv"
    path.write_text(
        "date,league,home_team,away_team,odds_home,odds_draw,odds_away\n"
        "2026-09-20,E0,Arsenal,Chelsea,2.30,3.40,3.10\n"
    )
    return path


@pytest.fixture
def multi_league_fallback_csv(tmp_path):
    path = tmp_path / "upcoming_multi.csv"
    path.write_text(
        "date,league,home_team,away_team,odds_home,odds_draw,odds_away\n"
        "2026-09-20,E0,Arsenal,Chelsea,2.30,3.40,3.10\n"
        "2026-09-20,SP1,Real Madrid,Barcelona,2.20,3.30,3.30\n"
        "2026-09-21,I1,Juventus,Inter,2.10,3.20,3.60\n"
    )
    return path


FREE_SCHEDULE_CSV = (
    "Div,Date,HomeTeam,AwayTeam,B365H,B365D,B365A\n"
    "E0,20/09/2026,Arsenal,Chelsea,2.10,3.60,3.40\n"
    "SP1,20/09/2026,Real Madrid,Barcelona,2.20,3.50,3.30\n"
    # Missing draw price — must be dropped, not filled with a placeholder.
    "E0,21/09/2026,Everton,Bournemouth,1.90,,4.50\n"
)


def _empty_frames(*_a, **_k):
    return pd.DataFrame(columns=odds_feed_mod.FIXTURE_COLUMNS)


class TestFetchFreeSchedule:
    def test_parses_and_filters_by_league(self):
        session = Mock()
        session.get.return_value = Mock(content=FREE_SCHEDULE_CSV.encode("latin1"), raise_for_status=Mock())

        df = fetch_free_schedule(["E0"], session=session)

        assert len(df) == 1  # SP1 row filtered out, E0 row with missing draw price dropped
        row = df.iloc[0]
        assert row["home_team"] == "ARS"
        assert row["away_team"] == "CHE"
        assert row["date"] == pd.Timestamp("2026-09-20")  # day-first parsed, not month-first
        assert row["odds_draw"] == pytest.approx(3.60)

    def test_drops_rows_with_incomplete_odds(self):
        session = Mock()
        session.get.return_value = Mock(content=FREE_SCHEDULE_CSV.encode("latin1"), raise_for_status=Mock())
        df = fetch_free_schedule(["E0", "SP1"], session=session)
        # 3 raw rows -> 1 dropped for missing B365D -> 2 remain
        assert len(df) == 2
        assert df["odds_draw"].notna().all()

    def test_network_failure_returns_empty_not_raise(self):
        session = Mock()
        session.get.side_effect = ConnectionError("blocked")
        df = fetch_free_schedule(["E0"], session=session)
        assert df.empty

    def test_unexpected_schema_returns_empty_not_raise(self):
        session = Mock()
        session.get.return_value = Mock(content=b"col1,col2\n1,2\n", raise_for_status=Mock())
        df = fetch_free_schedule(["E0"], session=session)
        assert df.empty

    def test_no_matching_league_returns_empty(self):
        session = Mock()
        session.get.return_value = Mock(content=FREE_SCHEDULE_CSV.encode("latin1"), raise_for_status=Mock())
        df = fetch_free_schedule(["D1"], session=session)
        assert df.empty


class TestGetUpcomingFixtures:
    def test_uses_live_odds_when_available(self, monkeypatch, fallback_csv):
        live_df = pd.DataFrame([{
            "date": "2026-09-21T15:00:00Z", "league": "E0", "home_team": "Arsenal", "away_team": "Bournemouth",
            "odds_home": 1.8, "odds_draw": 3.9, "odds_away": 4.2, "n_bookmakers": 4,
        }])
        monkeypatch.setattr(odds_feed_mod, "fetch_live_odds", lambda league, api_key=None: live_df)
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", _empty_frames)

        result, source = get_upcoming_fixtures(["E0"], api_key="test-key-1234567890abcdef", fallback_path=fallback_csv)

        assert source == FIXTURE_SOURCE_LIVE_ODDS
        assert len(result) == 1
        assert result.iloc[0]["away_team"] == "BOU"  # resolved via team_mappings, not the fallback file

    def test_falls_back_to_free_schedule_when_no_api_key(self, monkeypatch, fallback_csv):
        free_df = pd.DataFrame([{
            "date": pd.Timestamp("2026-09-20"), "league": "E0", "home_team": "ARS", "away_team": "CHE",
            "odds_home": 2.1, "odds_draw": 3.6, "odds_away": 3.4,
        }])
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", lambda leagues, **k: free_df)

        result, source = get_upcoming_fixtures(["E0"], api_key=None, fallback_path=fallback_csv)

        assert source == FIXTURE_SOURCE_FREE_SCHEDULE
        assert len(result) == 1

    def test_falls_back_to_free_schedule_when_live_feed_empty(self, monkeypatch, fallback_csv):
        free_df = pd.DataFrame([{
            "date": pd.Timestamp("2026-09-20"), "league": "E0", "home_team": "ARS", "away_team": "CHE",
            "odds_home": 2.1, "odds_draw": 3.6, "odds_away": 3.4,
        }])
        monkeypatch.setattr(odds_feed_mod, "fetch_live_odds", _empty_frames)
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", lambda leagues, **k: free_df)

        result, source = get_upcoming_fixtures(["E0", "SP1"], api_key="test-key-1234567890abcdef", fallback_path=fallback_csv)

        assert source == FIXTURE_SOURCE_FREE_SCHEDULE
        assert len(result) == 1

    def test_falls_back_to_sample_card_when_everything_else_empty(self, monkeypatch, fallback_csv):
        monkeypatch.setattr(odds_feed_mod, "fetch_live_odds", _empty_frames)
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", _empty_frames)

        result, source = get_upcoming_fixtures(["E0", "SP1"], api_key="test-key-1234567890abcdef", fallback_path=fallback_csv)

        assert source == FIXTURE_SOURCE_SAMPLE_CARD
        assert len(result) == 1  # from the fallback CSV, not an empty frame
        assert result.iloc[0]["home_team"] == "ARS"

    def test_invalid_key_falls_through_the_full_chain_end_to_end(self, monkeypatch, fallback_csv):
        """An invalid/rate-limited key (or a totally unreachable network,
        e.g. this sandbox) must never surface as an error to the caller —
        it should silently fall through live -> free -> sample card. Exercises
        the real fetch_live_odds/fetch_free_schedule HTTP-error handling
        (not mocked away), only the transport (requests.get) is faked."""
        resp = Mock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        fake_get = Mock(return_value=resp)
        monkeypatch.setattr(odds_feed_mod.requests, "get", fake_get)

        result, source = get_upcoming_fixtures(["E0"], api_key="invalid-key", fallback_path=fallback_csv)

        assert source == FIXTURE_SOURCE_SAMPLE_CARD
        assert len(result) == 1
        assert result.iloc[0]["home_team"] == "ARS"

    def test_queries_every_requested_league(self, monkeypatch, fallback_csv):
        calls = []

        def fake_fetch(league, api_key=None):
            calls.append(league)
            return pd.DataFrame(columns=odds_feed_mod.FIXTURE_COLUMNS)

        monkeypatch.setattr(odds_feed_mod, "fetch_live_odds", fake_fetch)
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", _empty_frames)
        get_upcoming_fixtures(["E0", "SP1", "I1"], api_key="test-key-1234567890abcdef", fallback_path=fallback_csv)
        assert calls == ["E0", "SP1", "I1"]

    def test_sample_card_is_filtered_to_requested_leagues(self, monkeypatch, multi_league_fallback_csv):
        # A league the model wasn't trained on (not requested here) must
        # not leak into the sample-card fallback tier — that team pair
        # would be "unseen" to the model and get a meaningless generic
        # prediction instead of a real one.
        monkeypatch.setattr(odds_feed_mod, "fetch_live_odds", _empty_frames)
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", _empty_frames)

        result, source = get_upcoming_fixtures(
            ["SP1", "I1"], api_key="test-key-1234567890abcdef", fallback_path=multi_league_fallback_csv,
        )

        assert source == FIXTURE_SOURCE_SAMPLE_CARD
        assert set(result["league"]) == {"SP1", "I1"}
        assert len(result) == 2

    def test_sample_card_unfiltered_when_nothing_matches_requested_leagues(self, monkeypatch, multi_league_fallback_csv):
        monkeypatch.setattr(odds_feed_mod, "fetch_live_odds", _empty_frames)
        monkeypatch.setattr(odds_feed_mod, "fetch_free_schedule", _empty_frames)

        result, source = get_upcoming_fixtures(
            ["D1"], api_key="test-key-1234567890abcdef", fallback_path=multi_league_fallback_csv,
        )

        assert source == FIXTURE_SOURCE_SAMPLE_CARD
        assert result.empty

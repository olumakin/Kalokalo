import json
from unittest.mock import Mock

import pandas as pd
import pytest

from src.ingestion.understat_xg import (
    extract_json_variable,
    fetch_league_season_xg,
)


def _encode_like_understat(obj) -> str:
    """Build the \\xHH-escaped payload Understat embeds in its pages,
    from a plain Python object — the inverse of what the module decodes."""
    payload_bytes = json.dumps(obj).encode("utf-8")
    return "".join(f"\\x{b:02x}" for b in payload_bytes)


def _fake_page(dates_data: list[dict]) -> str:
    escaped = _encode_like_understat(dates_data)
    # Include a second, irrelevant JSON.parse call to make sure the
    # variable-name lookback actually discriminates between them.
    return (
        "<html><script>"
        "var teamsData = JSON.parse('\\x7b\\x7d');"
        f"var datesData = JSON.parse('{escaped}');"
        "</script></html>"
    )


SAMPLE_MATCHES = [
    {
        "isResult": True,
        "datetime": "2023-08-12 15:00:00",
        "h": {"title": "Arsenal"},
        "a": {"title": "Manchester City"},
        "xG": {"h": "1.73", "a": "0.92"},
    },
    {
        "isResult": True,
        "datetime": "2023-08-19 15:00:00",
        "h": {"title": "Chelsea"},
        "a": {"title": "Liverpool"},
        "xG": {"h": "2.10", "a": "1.05"},
    },
    {
        # Fixture not yet played — must be dropped.
        "isResult": False,
        "datetime": "2023-08-26 15:00:00",
        "h": {"title": "Everton"},
        "a": {"title": "Bournemouth"},
        "xG": {"h": "0", "a": "0"},
    },
]


class TestExtractJsonVariable:
    def test_decodes_matching_variable(self):
        html = _fake_page(SAMPLE_MATCHES)
        result = extract_json_variable(html, "datesData")
        assert result == SAMPLE_MATCHES

    def test_raises_when_variable_absent(self):
        html = "<html><script>var somethingElse = JSON.parse('\\x7b\\x7d');</script></html>"
        with pytest.raises(ValueError):
            extract_json_variable(html, "datesData")


class TestFetchLeagueSeasonXg:
    def test_parses_results_only(self):
        html = _fake_page(SAMPLE_MATCHES)
        session = Mock()
        session.get.return_value = Mock(text=html, raise_for_status=Mock())

        df = fetch_league_season_xg("E0", 2023, session=session)

        assert len(df) == 2  # the isResult=False fixture is dropped
        assert set(df["home_team_raw"]) == {"Arsenal", "Chelsea"}
        arsenal_row = df[df["home_team_raw"] == "Arsenal"].iloc[0]
        assert arsenal_row["home_xg"] == pytest.approx(1.73)
        assert arsenal_row["away_xg"] == pytest.approx(0.92)
        assert arsenal_row["date"] == pd.Timestamp("2023-08-12")

    def test_unknown_league_returns_empty(self):
        df = fetch_league_season_xg("XX", 2023)
        assert df.empty

    def test_network_failure_returns_empty_not_raise(self):
        session = Mock()
        session.get.side_effect = ConnectionError("blocked")
        df = fetch_league_season_xg("E0", 2023, session=session)
        assert df.empty

    def test_malformed_page_returns_empty_not_raise(self):
        session = Mock()
        session.get.return_value = Mock(text="<html>no data here</html>", raise_for_status=Mock())
        df = fetch_league_season_xg("E0", 2023, session=session)
        assert df.empty

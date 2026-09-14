import pandas as pd
import pytest

import src.ingestion.sources as sources
from src.ingestion.sources import (
    BLEND_CONSENSUS,
    BLEND_STRICT,
    SOURCE_FOOTBALL_DATA,
    SOURCE_UNDERSTAT,
    load_and_blend_sources,
)

RAW_COLUMNS = [
    "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
    "B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA",
]


def _raw_football_data() -> pd.DataFrame:
    rows = [
        # (date, home, away, hg, ag)
        ("12/08/2023", "Arsenal", "Man City", 2, 1),
        ("19/08/2023", "Chelsea", "Liverpool", 1, 1),
        ("26/08/2023", "Everton", "Bournemouth", 0, 0),
    ]
    df = pd.DataFrame(rows, columns=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    df["FTR"] = df.apply(lambda r: "H" if r.FTHG > r.FTAG else ("A" if r.FTHG < r.FTAG else "D"), axis=1)
    for c in ["B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA"]:
        df[c] = 2.0
    df["league"] = "E0"
    df["season"] = "2324"
    return df


def _understat_xg() -> pd.DataFrame:
    # Only covers 2 of the 3 base matches (Everton vs Bournemouth is missing),
    # to distinguish strict vs. consensus blending.
    return pd.DataFrame([
        {
            "date": pd.Timestamp("2023-08-12"), "home_team_raw": "Arsenal", "away_team_raw": "Man City",
            "home_xg": 1.8, "away_xg": 1.1,
        },
        {
            "date": pd.Timestamp("2023-08-19"), "home_team_raw": "Chelsea", "away_team_raw": "Liverpool",
            "home_xg": 1.2, "away_xg": 1.4,
        },
    ])


@pytest.fixture
def settings():
    return {"data_source": {"cache_dir": "data/historical"}}


class TestLoadAndBlendSources:
    def test_football_data_only_returns_base_unchanged(self, monkeypatch, settings):
        monkeypatch.setattr(sources, "load_all", lambda *a, **k: _raw_football_data())

        result = load_and_blend_sources([SOURCE_FOOTBALL_DATA], ["E0"], 1, settings)

        assert len(result) == 3
        assert "home_xg" not in result.columns

    def test_consensus_blend_keeps_all_base_rows_with_nan_gaps(self, monkeypatch, settings):
        monkeypatch.setattr(sources, "load_all", lambda *a, **k: _raw_football_data())
        monkeypatch.setattr(sources, "fetch_league_season_xg", lambda league, season, **k: _understat_xg())

        result = load_and_blend_sources(
            [SOURCE_FOOTBALL_DATA, SOURCE_UNDERSTAT], ["E0"], 1, settings, blend_mode=BLEND_CONSENSUS,
        )

        assert len(result) == 3  # every base match kept
        assert "home_xg" in result.columns
        everton_row = result[result["home_team"] == "EVE"].iloc[0]
        assert pd.isna(everton_row["home_xg"])  # no Understat coverage for this match
        arsenal_row = result[result["home_team"] == "ARS"].iloc[0]
        assert arsenal_row["home_xg"] == pytest.approx(1.8)

    def test_strict_blend_drops_matches_missing_from_supplemental_source(self, monkeypatch, settings):
        monkeypatch.setattr(sources, "load_all", lambda *a, **k: _raw_football_data())
        monkeypatch.setattr(sources, "fetch_league_season_xg", lambda league, season, **k: _understat_xg())

        result = load_and_blend_sources(
            [SOURCE_FOOTBALL_DATA, SOURCE_UNDERSTAT], ["E0"], 1, settings, blend_mode=BLEND_STRICT,
        )

        assert len(result) == 2  # Everton/Bournemouth dropped — no Understat row
        assert set(result["home_team"]) == {"ARS", "CHE"}
        assert result["home_xg"].notna().all()

    def test_understat_unavailable_degrades_gracefully_under_consensus(self, monkeypatch, settings):
        monkeypatch.setattr(sources, "load_all", lambda *a, **k: _raw_football_data())
        monkeypatch.setattr(sources, "fetch_league_season_xg", lambda league, season, **k: pd.DataFrame(
            columns=["date", "home_team_raw", "away_team_raw", "home_xg", "away_xg"]
        ))

        result = load_and_blend_sources(
            [SOURCE_FOOTBALL_DATA, SOURCE_UNDERSTAT], ["E0"], 1, settings, blend_mode=BLEND_CONSENSUS,
        )

        assert len(result) == 3  # base results are not lost just because Understat failed

    def test_understat_unavailable_under_strict_yields_empty(self, monkeypatch, settings):
        monkeypatch.setattr(sources, "load_all", lambda *a, **k: _raw_football_data())
        monkeypatch.setattr(sources, "fetch_league_season_xg", lambda league, season, **k: pd.DataFrame(
            columns=["date", "home_team_raw", "away_team_raw", "home_xg", "away_xg"]
        ))

        result = load_and_blend_sources(
            [SOURCE_FOOTBALL_DATA, SOURCE_UNDERSTAT], ["E0"], 1, settings, blend_mode=BLEND_STRICT,
        )

        assert result.empty

    def test_empty_base_returns_empty(self, monkeypatch, settings):
        monkeypatch.setattr(sources, "load_all", lambda *a, **k: pd.DataFrame())

        result = load_and_blend_sources([SOURCE_FOOTBALL_DATA], ["E0"], 1, settings)

        assert result.empty

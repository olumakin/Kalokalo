import pandas as pd

from src.ingestion.normalizer import CANONICAL_COLUMNS, normalize_dataframe


def _raw_row(**odds_cols) -> pd.DataFrame:
    row = {
        "Date": "12/08/2023", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "FTHG": 2, "FTAG": 1, "FTR": "H", "league": "E0", "season": "2324",
    }
    row.update(odds_cols)
    return pd.DataFrame([row])


class TestNormalizeDataframe:
    def test_empty_input_returns_empty_canonical_frame(self):
        out = normalize_dataframe(pd.DataFrame())
        assert out.empty
        assert list(out.columns) == CANONICAL_COLUMNS

    def test_all_rows_unparseable_returns_empty_without_raising(self):
        raw = pd.DataFrame([{"Date": "not-a-date", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
                              "FTHG": 2, "FTAG": 1, "league": "E0", "season": "2324"}])
        out = normalize_dataframe(raw)
        assert out.empty
        assert list(out.columns) == CANONICAL_COLUMNS

    def test_odds_hierarchy_prefers_market_average(self):
        raw = _raw_row(AvgH=2.0, AvgD=3.3, AvgA=3.8, B365H=2.1, B365D=3.2, B365A=3.7)
        out = normalize_dataframe(raw)
        row = out.iloc[0]
        assert (row["odds_home"], row["odds_draw"], row["odds_away"]) == (2.0, 3.3, 3.8)
        assert row["price_source"] == "market average"

    def test_odds_hierarchy_falls_back_when_average_missing(self):
        raw = _raw_row(B365H=2.1, B365D=3.2, B365A=3.7)
        out = normalize_dataframe(raw)
        row = out.iloc[0]
        assert (row["odds_home"], row["odds_draw"], row["odds_away"]) == (2.1, 3.2, 3.7)
        assert row["price_source"] == "Bet365"

    def test_no_odds_columns_leaves_odds_and_source_null(self):
        raw = _raw_row()
        out = normalize_dataframe(raw)
        row = out.iloc[0]
        assert pd.isna(row["odds_home"]) and pd.isna(row["odds_draw"]) and pd.isna(row["odds_away"])
        assert pd.isna(row["price_source"])

    def test_home_goals_result_and_canonical_columns(self):
        raw = _raw_row(B365H=2.1, B365D=3.2, B365A=3.7)
        out = normalize_dataframe(raw)
        assert list(out.columns) == CANONICAL_COLUMNS
        row = out.iloc[0]
        assert (row["home_goals"], row["away_goals"], row["result"]) == (2, 1, "H")

    def test_monza_and_monaco_have_distinct_canonical_codes(self):
        """A03 regression: Monza (I1) and Monaco (F1) must have distinct canonical codes."""
        from src.ingestion.normalizer import load_team_mappings, resolve_team
        mappings = load_team_mappings()
        monza_code = resolve_team("Monza", "I1", mappings)
        monaco_code = resolve_team("Monaco", "F1", mappings)
        assert monza_code != monaco_code
        assert monza_code == "MNZ"
        assert monaco_code == "MON"

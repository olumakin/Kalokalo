import pandas as pd
import pytest

from src.ingestion.data_loader import select_entry_close_odds, select_match_odds, validate_matches


class TestSelectMatchOdds:
    def test_prefers_market_average_when_present(self):
        row = pd.Series({
            "AvgH": 2.0, "AvgD": 3.3, "AvgA": 3.8,
            "B365H": 2.1, "B365D": 3.2, "B365A": 3.7,
        })
        h, d, a, source = select_match_odds(row)
        assert (h, d, a) == (2.0, 3.3, 3.8)
        assert source == "market average"

    def test_falls_back_to_legacy_betbrain_average(self):
        row = pd.Series({"BbAvH": 2.05, "BbAvD": 3.25, "BbAvA": 3.75})
        h, d, a, source = select_match_odds(row)
        assert (h, d, a) == (2.05, 3.25, 3.75)
        assert "Betbrain" in source

    def test_falls_back_to_bet365_when_no_average_columns_exist(self):
        row = pd.Series({"B365H": 2.1, "B365D": 3.2, "B365A": 3.7})
        h, d, a, source = select_match_odds(row)
        assert (h, d, a) == (2.1, 3.2, 3.7)
        assert source == "Bet365"

    def test_skips_a_tier_with_a_partially_missing_price(self):
        # AvgH/AvgD present but AvgA missing -> whole tier is skipped,
        # not silently paired with a different tier's away price.
        row = pd.Series({
            "AvgH": 2.0, "AvgD": 3.3, "AvgA": None,
            "B365H": 2.1, "B365D": 3.2, "B365A": 3.7,
        })
        h, d, a, source = select_match_odds(row)
        assert (h, d, a) == (2.1, 3.2, 3.7)
        assert source == "Bet365"

    def test_returns_none_tuple_when_no_tier_available(self):
        row = pd.Series({"HomeTeam": "Arsenal", "AwayTeam": "Chelsea"})
        assert select_match_odds(row) == (None, None, None, None)

    def test_max_is_last_resort_below_bet365(self):
        row = pd.Series({"B365H": 2.1, "B365D": 3.2, "B365A": 3.7, "MaxH": 2.3, "MaxD": 3.6, "MaxA": 4.0})
        h, d, a, source = select_match_odds(row)
        assert source == "Bet365"


class TestSelectEntryCloseOdds:
    def test_prefers_pinnacle_open_close_pair(self):
        row = pd.Series({
            "PSH": 2.05, "PSD": 3.35, "PSA": 3.75,
            "PSCH": 2.10, "PSCD": 3.25, "PSCA": 3.65,
            "B365H": 2.0, "B365D": 3.3, "B365A": 3.8,
        })
        out = select_entry_close_odds(row)
        assert (out["entry_home"], out["entry_draw"], out["entry_away"]) == (2.05, 3.35, 3.75)
        assert out["entry_source"] == "Pinnacle (opening)"
        assert (out["close_home"], out["close_draw"], out["close_away"]) == (2.10, 3.25, 3.65)
        assert out["close_source"] == "Pinnacle closing"

    def test_entry_falls_back_to_bet365_when_no_pinnacle(self):
        row = pd.Series({"B365H": 2.0, "B365D": 3.3, "B365A": 3.8})
        out = select_entry_close_odds(row)
        assert (out["entry_home"], out["entry_draw"], out["entry_away"]) == (2.0, 3.3, 3.8)
        assert out["entry_source"] == "Bet365"

    def test_close_falls_back_to_market_average_when_no_pinnacle_closing(self):
        row = pd.Series({"AvgH": 2.1, "AvgD": 3.2, "AvgA": 3.7})
        out = select_entry_close_odds(row)
        assert out["close_source"] == "market average"

    def test_retail_draw_is_bet365_draw_specifically(self):
        row = pd.Series({"B365H": 2.0, "B365D": 3.3, "B365A": 3.8})
        out = select_entry_close_odds(row)
        assert out["retail_draw"] == pytest.approx(3.3)

    def test_missing_retail_price_is_none(self):
        row = pd.Series({"PSH": 2.0, "PSD": 3.3, "PSA": 3.8})
        out = select_entry_close_odds(row)
        assert out["retail_draw"] is None

    def test_no_available_tier_returns_none_fields(self):
        row = pd.Series({"HomeTeam": "Arsenal"})
        out = select_entry_close_odds(row)
        assert out["entry_home"] is None and out["close_home"] is None and out["retail_draw"] is None


class TestValidateMatches:
    def _base_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "date": pd.Timestamp("2024-01-01"), "league": "E0", "season": "2324",
                "home_team": "ARS", "away_team": "CHE", "home_goals": 2, "away_goals": 1, "result": "H",
                "odds_home": 2.0, "odds_draw": 3.3, "odds_away": 3.8, "price_source": "market average",
            },
            {
                "date": pd.Timestamp("2024-01-08"), "league": "E0", "season": "2324",
                "home_team": "LIV", "away_team": "MCI", "home_goals": 1, "away_goals": 1, "result": "D",
                "odds_home": 2.5, "odds_draw": 3.1, "odds_away": 2.9, "price_source": "Bet365",
            },
        ])

    def test_valid_rows_pass_through_unchanged(self):
        df = self._base_df()
        out, report = validate_matches(df)
        assert len(out) == 2
        assert report["output_rows"] == 2
        assert report["dropped_missing_core"] == 0
        assert report["odds_coverage"] == 2

    def test_drops_row_with_missing_core_field(self):
        df = self._base_df()
        df.loc[0, "home_goals"] = None
        out, report = validate_matches(df)
        assert len(out) == 1
        assert report["dropped_missing_core"] == 1

    def test_drops_row_with_negative_goals(self):
        df = self._base_df()
        df.loc[0, "away_goals"] = -1
        out, report = validate_matches(df)
        assert len(out) == 1
        assert report["dropped_missing_core"] == 1

    def test_drops_same_team_fixture(self):
        df = self._base_df()
        df.loc[0, "away_team"] = df.loc[0, "home_team"]
        out, report = validate_matches(df)
        assert len(out) == 1
        assert report["dropped_same_team"] == 1

    def test_drops_exact_duplicate_keeping_first(self):
        df = pd.concat([self._base_df(), self._base_df().iloc[[0]]], ignore_index=True)
        out, report = validate_matches(df)
        assert len(out) == 2
        assert report["dropped_duplicate"] == 1

    def test_neutralizes_implausible_odds_without_dropping_the_match(self):
        df = self._base_df()
        df.loc[0, "odds_draw"] = 1.0  # not a valid decimal price
        out, report = validate_matches(df)
        assert len(out) == 2  # match itself is still kept
        assert report["odds_invalidated"] == 1
        row = out[out["home_team"] == "ARS"].iloc[0]
        assert pd.isna(row["odds_home"]) and pd.isna(row["odds_draw"]) and pd.isna(row["odds_away"])
        assert pd.isna(row["price_source"])

    def test_empty_input_returns_empty_with_zeroed_report(self):
        out, report = validate_matches(pd.DataFrame())
        assert out.empty
        assert report["input_rows"] == 0
        assert report["output_rows"] == 0

    def test_missing_odds_columns_does_not_raise(self):
        df = self._base_df().drop(columns=["odds_home", "odds_draw", "odds_away"])
        out, report = validate_matches(df)
        assert len(out) == 2
        assert report["odds_coverage"] == 0

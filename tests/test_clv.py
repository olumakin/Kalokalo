import numpy as np
import pandas as pd
import pytest

from src.validation.clv import evaluate_clv_series, evaluate_league_clv, two_price_clv


class TestTwoPriceClv:
    def test_entry_equal_to_close_gives_zero(self):
        assert two_price_clv(3.4, 3.4) == pytest.approx(0.0)

    def test_entry_above_close_gives_positive_clv(self):
        # A better (higher) price than where the market closed -- the
        # market moved in the bettor's favor after the bet was placed.
        assert two_price_clv(3.6, 3.4) > 0.0

    def test_entry_below_close_gives_negative_clv(self):
        assert two_price_clv(3.2, 3.4) < 0.0

    def test_matches_known_value(self):
        assert two_price_clv(3.4, 3.4) == pytest.approx(0.0)
        assert two_price_clv(3.74, 3.4) == pytest.approx(0.1, abs=1e-6)


class TestEvaluateClvSeries:
    def test_insufficient_sample_below_threshold(self):
        result = evaluate_clv_series(np.array([0.01] * 10))
        assert result["insufficient_sample"] is True
        assert np.isnan(result["mean_clv"])
        assert np.isnan(result["ci_lo"]) and np.isnan(result["ci_hi"])

    def test_sufficient_sample_reports_real_numbers(self):
        rng = np.random.default_rng(0)
        clv = rng.normal(0.02, 0.05, 50)
        result = evaluate_clv_series(clv)
        assert result["insufficient_sample"] is False
        assert result["n"] == 50
        assert not np.isnan(result["mean_clv"])
        assert result["ci_lo"] <= result["mean_clv"] <= result["ci_hi"]

    def test_block_ids_uses_block_bootstrap(self):
        # 12 rows in 2 blocks of 6 -- exercises the block_bootstrap_ci
        # path rather than the plain row-level one.
        clv = np.array([0.5] * 6 + [-0.5] * 6)
        block_ids = np.array(["mw1"] * 6 + ["mw2"] * 6)
        result = evaluate_clv_series(clv, block_ids=block_ids)
        assert result["insufficient_sample"] is False
        assert result["ci_lo"] >= -0.5 and result["ci_hi"] <= 0.5


class TestEvaluateLeagueClv:
    def _df(self) -> pd.DataFrame:
        rng = np.random.default_rng(1)
        n = 60
        close = rng.uniform(2.0, 4.0, n)
        entry = close * (1 + rng.normal(0.0, 0.02, n))  # baseline: ~centered on 0
        flagged_entry = close * 1.05  # flagged bets: a genuine, deliberate edge
        return pd.DataFrame({
            "entry_odds": entry, "close_odds": close, "flagged_entry": flagged_entry,
            "matchweek": [f"mw{i // 10}" for i in range(n)],
            "qualified": [i % 3 == 0 for i in range(n)],
        })

    def test_baseline_and_flagged_both_computed(self):
        df = self._df()
        result = evaluate_league_clv(df, "entry_odds", "close_odds", df["qualified"])
        assert "baseline" in result and "flagged" in result
        assert result["baseline"]["n"] == 60
        assert result["flagged"]["n"] == df["qualified"].sum()

    def test_rows_missing_either_price_are_excluded_from_both(self):
        df = self._df()
        df.loc[0, "entry_odds"] = np.nan
        df.loc[1, "close_odds"] = np.nan
        result = evaluate_league_clv(df, "entry_odds", "close_odds", df["qualified"])
        assert result["baseline"]["n"] == 58

    def test_flagged_bets_with_genuine_edge_show_higher_mean_clv_than_baseline(self):
        df = self._df()
        # Use the deliberately-better flagged_entry price only for
        # "flagged" rows to simulate a model that finds real value.
        df["entry_for_flagged"] = np.where(df["qualified"], df["flagged_entry"], df["entry_odds"])
        result = evaluate_league_clv(df, "entry_for_flagged", "close_odds", df["qualified"])
        assert result["flagged"]["mean_clv"] > result["baseline"]["mean_clv"]

    def test_no_block_column_falls_back_to_row_level_bootstrap(self):
        df = self._df().drop(columns=["matchweek"])
        result = evaluate_league_clv(df, "entry_odds", "close_odds", df["qualified"], block_col="matchweek")
        assert result["baseline"]["insufficient_sample"] is False

import sys

import pandas as pd
import pytest

sys.path.insert(0, "tests")
from test_dixon_coles import generate_synthetic_matches  # noqa: E402

from src.models.dixon_coles import DixonColesModel  # noqa: E402
from src.pipeline import build_predictions  # noqa: E402

SETTINGS = {
    "devig": {"method": "multiplicative"},
    "edge": {"min_ev": 0.03, "kelly_fraction": 0.15, "single_match_cap": 0.025, "daily_slate_cap": 0.08},
}


@pytest.fixture
def fitted_model():
    matches = generate_synthetic_matches()
    return DixonColesModel(min_matches=15).fit(matches, xi=0.0065)


@pytest.fixture
def fixtures():
    return pd.DataFrame([
        {
            "date": pd.Timestamp("2026-09-20"), "league": "E0", "home_team": "T00", "away_team": "T01",
            "odds_home": 2.30, "odds_draw": 3.40, "odds_away": 3.10,
        },
        {
            "date": pd.Timestamp("2026-09-21"), "league": "E0", "home_team": "T02", "away_team": "T03",
            "odds_home": 2.60, "odds_draw": 3.20, "odds_away": 2.80,
        },
    ])


class TestBuildPredictionsScorelines:
    def test_includes_real_scoreline_columns(self, fitted_model, fixtures):
        predictions = build_predictions(fixtures, fitted_model, SETTINGS)

        for col in ["top_score", "top_score_prob", "alt_score", "alt_score_prob"]:
            assert col in predictions.columns

        for _, row in predictions.iterrows():
            # "H-A" format, both non-negative integers
            h, a = row["top_score"].split("-")
            assert int(h) >= 0 and int(a) >= 0
            h2, a2 = row["alt_score"].split("-")
            assert int(h2) >= 0 and int(a2) >= 0
            # Top scoreline is at least as probable as the runner-up.
            assert row["top_score_prob"] >= row["alt_score_prob"]
            assert row["top_score"] != row["alt_score"]

    def test_top_score_prob_is_a_real_probability(self, fitted_model, fixtures):
        predictions = build_predictions(fixtures, fitted_model, SETTINGS)
        assert (predictions["top_score_prob"] > 0).all()
        assert (predictions["top_score_prob"] <= 1).all()

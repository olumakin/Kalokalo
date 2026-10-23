import sys

import pandas as pd
import pytest

sys.path.insert(0, "tests")
from test_dixon_coles import generate_synthetic_matches  # noqa: E402

from src.models.dixon_coles import DixonColesModel  # noqa: E402
from src.pipeline import build_predictions, fit_model, trim_to_rolling_window  # noqa: E402

SETTINGS = {
    "devig": {"method": "multiplicative"},
    "edge": {"min_ev": 0.03, "kelly_fraction": 0.15, "single_match_cap": 0.025, "daily_slate_cap": 0.08},
}

FIT_SETTINGS = {
    "model": {
        "min_matches_for_team_rating": 15, "max_optimizer_iterations": 500,
        "optimizer_method": "L-BFGS-B", "rho_init": -0.05, "xi_decay": 0.0065,
    },
}


@pytest.fixture
def fitted_model():
    matches = generate_synthetic_matches()
    return DixonColesModel(min_matches=15).fit(matches, xi=0.0065)


@pytest.fixture
def models(fitted_model):
    # build_predictions takes {league: model} since src.pipeline.fit_model
    # fits per league, not one shared model -- the `fixtures` fixture
    # below is all-E0, so one entry covers it.
    return {"E0": fitted_model}


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
    def test_includes_real_scoreline_columns(self, models, fixtures):
        predictions = build_predictions(fixtures, models, SETTINGS)

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

    def test_top_score_prob_is_a_real_probability(self, models, fixtures):
        predictions = build_predictions(fixtures, models, SETTINGS)
        assert (predictions["top_score_prob"] > 0).all()
        assert (predictions["top_score_prob"] <= 1).all()


class TestCalibrationStakeMultiplier:
    def test_discounts_only_the_configured_league(self, fitted_model):
        # Caps disabled (set far above anything a single bet could reach)
        # so the 2x relationship between leagues isn't masked by
        # apply_risk_caps clipping one side and not the other.
        settings = {
            "devig": {"method": "multiplicative"},
            "edge": {
                "min_ev": 0.03, "kelly_fraction": 0.15,
                "single_match_cap": 1.0, "daily_slate_cap": 1.0,
                "calibration_stake_multiplier": {"D1": 0.5},
            },
        }
        # This fitted_model gives T00-vs-T01 a model draw probability of
        # ~0.225 (fair odds ~4.44) -- odds_draw=5.5 with a market that
        # devigs to a draw probability well below that is a comfortable,
        # unambiguous qualifying bet (EV ~+24%), not a boundary case that
        # might tip either side of qualifies()'s >= 0.03 threshold.
        same_match = {
            "home_team": "T00", "away_team": "T01",
            "odds_home": 1.80, "odds_draw": 5.50, "odds_away": 4.50,
        }
        fixtures = pd.DataFrame([
            {"date": pd.Timestamp("2026-09-20"), "league": "E0", **same_match},
            {"date": pd.Timestamp("2026-09-21"), "league": "D1", **same_match},
        ])
        # Same underlying fit for both leagues -- isolates the discount's
        # effect from any difference a separately-fit D1 model would add.
        models = {"E0": fitted_model, "D1": fitted_model}

        predictions = build_predictions(fixtures, models, settings)
        e0 = predictions[predictions["league"] == "E0"].iloc[0]
        d1 = predictions[predictions["league"] == "D1"].iloc[0]

        assert e0["qualified"]  # the discount comparison is meaningless on a non-qualifying bet
        assert d1["qualified"]
        assert d1["kelly_raw"] == pytest.approx(e0["kelly_raw"] * 0.5, rel=1e-6)
        assert d1["stake_pct"] == pytest.approx(e0["stake_pct"] * 0.5, rel=1e-6)

    def test_unlisted_leagues_are_unaffected(self, models, fixtures):
        settings = {**SETTINGS, "edge": {**SETTINGS["edge"], "calibration_stake_multiplier": {"D1": 0.5}}}
        discounted = build_predictions(fixtures, models, settings)
        baseline = build_predictions(fixtures, models, SETTINGS)
        # `fixtures` is all-E0 -- absent from the D1-only multiplier map.
        pd.testing.assert_series_equal(discounted["stake_pct"], baseline["stake_pct"])

    def test_missing_multiplier_config_is_backward_compatible(self, models, fixtures):
        # Module-level SETTINGS has no calibration_stake_multiplier key at
        # all -- must not raise (older configs, or settings.yaml edited
        # to remove it, shouldn't crash the pipeline).
        build_predictions(fixtures, models, SETTINGS)


class TestFitModelPerLeague:
    def test_fits_each_league_independently(self):
        # Two leagues, deliberately different seeds (so, e.g., mu0_
        # differs) -- fit_model must not pool them into one joint fit.
        e0 = generate_synthetic_matches(seed=42)
        d1 = generate_synthetic_matches(seed=7).assign(league="D1")
        matches = pd.concat([e0, d1], ignore_index=True)

        models = fit_model(matches, FIT_SETTINGS)

        assert set(models.keys()) == {"E0", "D1"}
        assert isinstance(models["E0"], DixonColesModel)
        assert isinstance(models["D1"], DixonColesModel)
        # Independently-fit models on differently-seeded data shouldn't
        # coincidentally land on the exact same baseline -- a shared/
        # joint fit would, by construction, give both the same mu0_.
        assert models["E0"].mu0_ != models["D1"].mu0_

    def test_one_league_failing_does_not_cost_the_others(self, monkeypatch):
        e0 = generate_synthetic_matches(seed=42)
        d1 = generate_synthetic_matches(seed=7).assign(league="D1")
        matches = pd.concat([e0, d1], ignore_index=True)

        real_fit = DixonColesModel.fit

        def flaky_fit(self, league_matches, *args, **kwargs):
            if (league_matches["league"] == "D1").all():
                raise ValueError("synthetic failure for this test")
            return real_fit(self, league_matches, *args, **kwargs)

        monkeypatch.setattr(DixonColesModel, "fit", flaky_fit)

        models = fit_model(matches, FIT_SETTINGS)
        assert set(models.keys()) == {"E0"}  # D1 skipped, not raised

    def test_every_league_failing_raises(self, monkeypatch):
        matches = generate_synthetic_matches()

        def always_fails(self, *args, **kwargs):
            raise ValueError("synthetic failure for this test")

        monkeypatch.setattr(DixonColesModel, "fit", always_fails)

        with pytest.raises(ValueError, match="No league could be fit"):
            fit_model(matches, FIT_SETTINGS)


class TestFitModelPromotedPrior:
    def test_promoted_prior_by_league_is_passed_to_the_right_league_s_model(self):
        e0 = generate_synthetic_matches(seed=42)
        d1 = generate_synthetic_matches(seed=7).assign(league="D1")
        matches = pd.concat([e0, d1], ignore_index=True)

        models = fit_model(matches, FIT_SETTINGS, promoted_prior_by_league={"E0": (-0.4, 0.4)})

        assert models["E0"].promoted_prior == (-0.4, 0.4)
        # D1 has no entry in the map -- must keep the original (0, 0)
        # league-median target, not crash or reuse E0's prior.
        assert models["D1"].promoted_prior == (0.0, 0.0)

    def test_omitted_promoted_prior_by_league_is_backward_compatible(self):
        matches = generate_synthetic_matches()
        models = fit_model(matches, FIT_SETTINGS)
        assert models["E0"].promoted_prior == (0.0, 0.0)


class TestBuildPredictionsMissingModel:
    def test_a_league_with_no_fitted_model_is_skipped_not_raised(self, models, fixtures):
        extra = pd.DataFrame([{
            "date": pd.Timestamp("2026-09-22"), "league": "SP1", "home_team": "T00", "away_team": "T01",
            "odds_home": 2.30, "odds_draw": 3.40, "odds_away": 3.10,
        }])
        mixed_fixtures = pd.concat([fixtures, extra], ignore_index=True)

        predictions = build_predictions(mixed_fixtures, models, SETTINGS)  # models only has "E0"

        assert "SP1" not in predictions["league"].values
        assert set(predictions["league"]) == {"E0"}


class TestTrimToRollingWindow:
    def _matches(self, as_of: pd.Timestamp) -> pd.DataFrame:
        # One row per day for 1000 days leading up to (but not
        # including) as_of, plus one row exactly on as_of itself.
        dates = pd.date_range(end=as_of, periods=1001, freq="D", tz="UTC")
        return pd.DataFrame({"date": dates, "league": "E0", "home_team": "A", "away_team": "B"})

    def test_keeps_only_rows_within_the_window(self):
        as_of = pd.Timestamp("2026-10-23", tz="UTC")
        matches = self._matches(as_of)
        trimmed = trim_to_rolling_window(matches, window_days=730, as_of=as_of)

        assert (trimmed["date"] >= as_of - pd.Timedelta(days=730)).all()
        assert (trimmed["date"] < as_of).all()

    def test_row_exactly_on_as_of_is_excluded(self):
        # Mirrors gate_harness.run_league_evaluation's own boundary
        # (date < current_date), which treats "now" as not-yet-history.
        as_of = pd.Timestamp("2026-10-23", tz="UTC")
        matches = self._matches(as_of)
        trimmed = trim_to_rolling_window(matches, window_days=730, as_of=as_of)
        assert not (trimmed["date"] == as_of).any()

    def test_row_exactly_at_window_start_is_included(self):
        as_of = pd.Timestamp("2026-10-23", tz="UTC")
        matches = self._matches(as_of)
        trimmed = trim_to_rolling_window(matches, window_days=730, as_of=as_of)
        window_start = as_of - pd.Timedelta(days=730)
        assert (trimmed["date"] == window_start).any()

    def test_matches_a_known_count(self):
        # 730-day window, left-inclusive/right-exclusive -> exactly 730
        # distinct days' worth of the one-row-per-day fixture.
        as_of = pd.Timestamp("2026-10-23", tz="UTC")
        matches = self._matches(as_of)
        trimmed = trim_to_rolling_window(matches, window_days=730, as_of=as_of)
        assert len(trimmed) == 730

    def test_defaults_as_of_to_now(self):
        now = pd.Timestamp.now(tz="UTC")
        matches = self._matches(now)
        trimmed = trim_to_rolling_window(matches, window_days=730)
        assert not trimmed.empty
        assert (trimmed["date"] <= now).all()

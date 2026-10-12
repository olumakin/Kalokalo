import numpy as np
import pandas as pd
import pytest

from src.validation.gate_harness import (
    REASON_INSUFFICIENT_LEAGUE_HISTORY,
    REASON_UNSEEN_TEAM,
    _default_fit_func,
    assert_datetime_utc,
    get_code_version,
    run_gate_evaluation,
    run_league_evaluation,
)


def _priced_matches(n_teams=10, rounds=6, seed=42, league="E0", season="2324", start="2023-08-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    rows = []
    start_ts = pd.Timestamp(start, tz="UTC")
    day = 0
    for _ in range(rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                hg, ag = int(rng.poisson(1.4)), int(rng.poisson(1.1))
                close_d = float(rng.uniform(3.0, 3.6))
                entry_d = close_d * float(rng.normal(1.0, 0.02))
                rows.append({
                    "date": start_ts + pd.Timedelta(days=day), "league": league, "season": season,
                    "home_team": home, "away_team": away, "home_goals": hg, "away_goals": ag,
                    "result": "H" if hg > ag else ("A" if hg < ag else "D"),
                    "entry_home": 2.0, "entry_draw": entry_d, "entry_away": 3.8, "entry_source": "Pinnacle (opening)",
                    "close_home": 2.0, "close_draw": close_d, "close_away": 3.8, "close_source": "Pinnacle closing",
                    "retail_draw": close_d,
                })
                day += 1
    return pd.DataFrame(rows)


class TestAssertDatetimeUtc:
    def test_accepts_datetime64_column(self):
        df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"], utc=True)})
        assert_datetime_utc(df)  # must not raise

    def test_rejects_string_dates(self):
        df = pd.DataFrame({"date": ["2024-01-01"]})
        with pytest.raises(TypeError):
            assert_datetime_utc(df)

    def test_rejects_missing_column(self):
        with pytest.raises(ValueError):
            assert_datetime_utc(pd.DataFrame({"other": [1]}), col="date")


class TestGetCodeVersion:
    def test_returns_a_non_empty_string(self):
        # Either a real short hash or the "unknown" fallback -- never
        # raises regardless of whether .git is present.
        version = get_code_version()
        assert isinstance(version, str) and len(version) > 0


class TestRunLeagueEvaluation:
    def test_end_to_end_produces_results_with_no_exceptions(self):
        matches = _priced_matches()
        results, exclusions = run_league_evaluation(
            matches, "E0", xi=0.0065, min_train_matches=50, retrain_every_days=7,
        )
        assert not results.empty
        assert set(results["league"].unique()) == {"E0"}
        assert (results[["p_home", "p_draw", "p_away"]].sum(axis=1).round(6) == 1.0).all()

    def test_leakage_test_fit_func_sees_only_strictly_prior_data(self):
        """The core no-leakage guarantee: at every retrain point, the
        training data must be strictly older than the matchday that
        retrain point is about to predict, and must not contain that
        matchday's own fixtures."""
        matches = _priced_matches(n_teams=8, rounds=4)
        retrain_events = []

        def spy_on_retrain(current_date, train_df):
            retrain_events.append((current_date, train_df))

        run_league_evaluation(
            matches, "E0", xi=0.0065, min_train_matches=50, retrain_every_days=7,
            on_retrain=spy_on_retrain,
        )

        assert len(retrain_events) > 0
        for current_date, train_df in retrain_events:
            if train_df.empty:
                continue
            assert train_df["date"].max() < current_date
            assert current_date not in set(train_df["date"])

    def test_fit_func_itself_never_receives_the_final_matchday(self):
        """Complements the on_retrain-based test above by asserting
        directly inside fit_func — what the review specifically asked
        for ("a mock fit_func asserts...") — rather than only inspecting
        train_df from the outside via the hook."""
        matches = _priced_matches(n_teams=8, rounds=4)
        max_date = matches["date"].max()

        def asserting_fit_func(train_df, xi, warm_start, max_iter, min_matches, method):
            assert train_df["date"].max() < max_date, "fit_func received data up to the final matchday"
            return _default_fit_func(train_df, xi, warm_start, max_iter, min_matches, method)

        run_league_evaluation(
            matches, "E0", xi=0.0065, fit_func=asserting_fit_func, min_train_matches=50, retrain_every_days=7,
        )

    def test_unseen_team_is_excluded_not_silently_defaulted(self):
        matches = _priced_matches(n_teams=6, rounds=6)
        # Append one fixture for a brand-new team the model never trained on.
        extra = pd.DataFrame([{
            "date": matches["date"].max() + pd.Timedelta(days=1), "league": "E0", "season": "2324",
            "home_team": "NEWTEAM", "away_team": "T00", "home_goals": 1, "away_goals": 1, "result": "D",
            "entry_home": 2.0, "entry_draw": 3.3, "entry_away": 3.8, "entry_source": "Bet365",
            "close_home": 2.0, "close_draw": 3.3, "close_away": 3.8, "close_source": "Bet365",
            "retail_draw": 3.3,
        }])
        matches = pd.concat([matches, extra], ignore_index=True)

        results, exclusions = run_league_evaluation(matches, "E0", xi=0.0065, min_train_matches=50)

        assert "NEWTEAM" not in results["home_team"].values
        assert (exclusions["skip_reason"] == REASON_UNSEEN_TEAM).any()
        newteam_exclusion = exclusions[exclusions["fixture_id"].str.contains("NEWTEAM")]
        assert not newteam_exclusion.empty
        assert newteam_exclusion.iloc[0]["date"] == extra.iloc[0]["date"]
        assert newteam_exclusion.iloc[0]["season"] == "2324"

    def test_insufficient_history_is_excluded_with_reason_code(self):
        # min_train_matches deliberately higher than any window can supply.
        matches = _priced_matches(n_teams=4, rounds=2)
        results, exclusions = run_league_evaluation(matches, "E0", xi=0.0065, min_train_matches=100_000)
        assert results.empty
        assert (exclusions["skip_reason"] == REASON_INSUFFICIENT_LEAGUE_HISTORY).all()
        assert len(exclusions) == len(matches)

    def test_rejects_non_datetime_date_column(self):
        matches = _priced_matches()
        matches["date"] = matches["date"].astype(str)
        with pytest.raises(TypeError):
            run_league_evaluation(matches, "E0", xi=0.0065)

    def test_missing_price_leaves_row_present_with_nan_market_fields(self):
        matches = _priced_matches(n_teams=6, rounds=6)
        matches.loc[matches.index[-1], ["entry_home", "entry_draw", "entry_away"]] = np.nan
        results, _ = run_league_evaluation(matches, "E0", xi=0.0065, min_train_matches=50)
        nan_rows = results[results["entry_draw"].isna()]
        assert not nan_rows.empty
        assert nan_rows.iloc[0]["qualified"] == False  # noqa: E712
        assert np.isnan(nan_rows.iloc[0]["ev"])

    def test_cache_is_reused_on_second_run(self, tmp_path):
        matches = _priced_matches(n_teams=6, rounds=6)
        calls = {"n": 0}

        def counting_fit_func(train_df, xi, warm_start, max_iter, min_matches, method):
            calls["n"] += 1
            return _default_fit_func(train_df, xi, warm_start, max_iter, min_matches, method)

        run_league_evaluation(
            matches, "E0", xi=0.0065, fit_func=counting_fit_func, min_train_matches=50,
            cache_dir=tmp_path, code_version="test-version",
        )
        first_calls = calls["n"]
        assert first_calls > 0

        run_league_evaluation(
            matches, "E0", xi=0.0065, fit_func=counting_fit_func, min_train_matches=50,
            cache_dir=tmp_path, code_version="test-version",
        )
        # Second run hits the cache for every retrain point -- fit_func
        # (and therefore the optimizer) is never called again.
        assert calls["n"] == first_calls


class TestRunGateEvaluation:
    def test_runs_multiple_leagues_and_tags_each_row(self):
        e0 = _priced_matches(n_teams=6, rounds=6, league="E0", seed=1)
        sp1 = _priced_matches(n_teams=6, rounds=6, league="SP1", seed=2)
        matches = pd.concat([e0, sp1], ignore_index=True)

        results, exclusions = run_gate_evaluation(
            matches, xi_by_league={"E0": 0.005, "SP1": 0.008}, min_train_matches=50,
        )

        assert set(results["league"].unique()) == {"E0", "SP1"}
        assert (results[results["league"] == "E0"]["xi_used"] == 0.005).all()
        assert (results[results["league"] == "SP1"]["xi_used"] == 0.008).all()

    def test_every_row_carries_the_same_run_id(self):
        matches = _priced_matches(n_teams=6, rounds=6)
        results, _ = run_gate_evaluation(matches, xi_by_league={"E0": 0.0065}, min_train_matches=50)
        assert results["run_id"].nunique() == 1

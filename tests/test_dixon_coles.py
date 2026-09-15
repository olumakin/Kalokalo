import numpy as np
import pandas as pd
import pytest

from src.models.dixon_coles import DixonColesModel, tau
from src.models.simulator import build_score_matrix, top_scorelines


def generate_synthetic_matches(n_teams: int = 10, rounds: int = 6, seed: int = 42) -> pd.DataFrame:
    """Round-robin synthetic history with enough matches per team (>= 15)
    to exercise the full (non-regularized) fitting path."""
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    rows = []
    start = pd.Timestamp("2023-08-01")
    day = 0
    for r in range(rounds):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                home_goals = rng.poisson(1.4)
                away_goals = rng.poisson(1.1)
                rows.append({
                    "date": start + pd.Timedelta(days=day),
                    "league": "E0",
                    "season": "2324",
                    "home_team": teams[i],
                    "away_team": teams[j],
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "result": "H" if home_goals > away_goals else ("A" if home_goals < away_goals else "D"),
                    "odds_home": 2.0,
                    "odds_draw": 3.3,
                    "odds_away": 3.8,
                })
                day += 1
    return pd.DataFrame(rows)


class TestScoreMatrix:
    @pytest.mark.parametrize("lam,mu,rho", [
        (1.4, 1.1, -0.05),
        (0.8, 0.8, 0.1),
        (2.5, 0.3, -0.2),
        (1.0, 1.0, 0.0),
    ])
    def test_matrix_sums_to_one(self, lam, mu, rho):
        matrix = build_score_matrix(lam, mu, rho)
        assert matrix.sum() == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.parametrize("lam,mu,rho", [
        (1.4, 1.1, -0.05),
        (0.8, 0.8, 0.1),
        (2.5, 0.3, -0.2),
    ])
    def test_matrix_nonnegative(self, lam, mu, rho):
        matrix = build_score_matrix(lam, mu, rho)
        assert (matrix >= 0).all()

    def test_tau_known_values(self):
        lam = np.array([1.4])
        mu = np.array([1.1])
        rho = 0.1
        assert tau(np.array([0.0]), np.array([0.0]), lam, mu, rho)[0] == pytest.approx(1 - lam[0] * mu[0] * rho)
        assert tau(np.array([1.0]), np.array([1.0]), lam, mu, rho)[0] == pytest.approx(1 - rho)
        assert tau(np.array([2.0]), np.array([2.0]), lam, mu, rho)[0] == pytest.approx(1.0)


class TestTopScorelines:
    def test_returns_highest_probability_cells_in_order(self):
        matrix = np.zeros((10, 10))
        matrix[1, 1] = 0.20
        matrix[2, 1] = 0.15
        matrix[0, 0] = 0.10
        matrix[3, 3] = 0.01
        # remaining mass spread thinly so it never outranks the above
        remaining = 1.0 - matrix.sum()
        matrix[5, 5] = remaining

        top = top_scorelines(matrix, n=3)

        assert top[0] == (5, 5, pytest.approx(remaining))
        assert top[1] == (1, 1, pytest.approx(0.20))
        assert top[2] == (2, 1, pytest.approx(0.15))

    def test_n_controls_result_length(self):
        matrix = build_score_matrix(1.4, 1.1, -0.05)
        assert len(top_scorelines(matrix, n=1)) == 1
        assert len(top_scorelines(matrix, n=5)) == 5

    def test_matches_realistic_fit_favors_low_scorelines(self):
        # A realistic-ish low-scoring matchup should have its most likely
        # scoreline somewhere in the low-goal region, not a wild outlier.
        matrix = build_score_matrix(1.3, 1.0, -0.05)
        top = top_scorelines(matrix, n=1)
        h, a, p = top[0]
        assert h <= 3 and a <= 3
        assert p > 0


class TestDixonColesFit:
    def test_fit_produces_valid_parameters(self):
        matches = generate_synthetic_matches()
        model = DixonColesModel(min_matches=15, max_iter=500).fit(matches, xi=0.0065)

        assert isinstance(model.mu0_, float)
        assert isinstance(model.gamma_, float)
        assert isinstance(model.rho_, float)
        assert set(model.teams_) == set(matches["home_team"]).union(matches["away_team"])

        # sum-to-zero identifiability constraint
        assert np.mean(list(model.alpha_.values())) == pytest.approx(0.0, abs=1e-6)
        assert np.mean(list(model.beta_.values())) == pytest.approx(0.0, abs=1e-6)

    def test_gamma_by_season_has_one_entry_per_season_and_gamma_is_the_latest(self):
        # Two seasons with a deliberately different home-advantage
        # pattern: season 2223 has a strong home boost, season 2324 has
        # none at all.
        rng = np.random.default_rng(5)
        rows = []
        teams = [f"T{i:02d}" for i in range(8)]
        day = 0
        for season, home_lambda, away_lambda in (("2223", 2.4, 0.6), ("2324", 1.2, 1.2)):
            start = pd.Timestamp("2022-08-01") if season == "2223" else pd.Timestamp("2023-08-01")
            for _ in range(8):
                for i in teams:
                    for j in teams:
                        if i == j:
                            continue
                        rows.append({
                            "date": start + pd.Timedelta(days=day), "league": "E0", "season": season,
                            "home_team": i, "away_team": j,
                            "home_goals": int(rng.poisson(home_lambda)), "away_goals": int(rng.poisson(away_lambda)),
                            "result": "H",
                        })
                        day += 1
        matches = pd.DataFrame(rows)

        model = DixonColesModel(min_matches=15, max_iter=1000).fit(matches, xi=0.0)

        assert set(model.gamma_by_season_.keys()) == {"2223", "2324"}
        # 2223's true home/away goal ratio strongly favors the home side;
        # 2324's doesn't, so its fitted gamma should be much smaller.
        assert model.gamma_by_season_["2223"] > model.gamma_by_season_["2324"]
        # predict()/gamma_ use the most recently played season (2324, by date).
        assert model.gamma_ == pytest.approx(model.gamma_by_season_["2324"])

    def test_single_season_data_still_yields_one_gamma(self):
        # Backward-compatibility check: data with only one season value
        # (as all existing synthetic fixtures use) degenerates cleanly to
        # the old single-gamma behavior.
        matches = generate_synthetic_matches()
        model = DixonColesModel(min_matches=15).fit(matches, xi=0.0065)
        assert list(model.gamma_by_season_.keys()) == ["2324"]
        assert model.gamma_ == pytest.approx(model.gamma_by_season_["2324"])

    def test_predict_returns_positive_expected_goals(self):
        matches = generate_synthetic_matches()
        model = DixonColesModel(min_matches=15).fit(matches, xi=0.0065)
        lam, mu, rho = model.predict("T00", "T01")
        assert lam > 0
        assert mu > 0
        assert isinstance(rho, float)

    def test_unseen_team_defaults_to_league_median(self):
        matches = generate_synthetic_matches()
        model = DixonColesModel(min_matches=15).fit(matches, xi=0.0065)
        lam, mu, rho = model.predict("UNKNOWN_TEAM", "T01")
        # alpha/beta for unseen team default to 0 -> lambda determined solely
        # by opponent + home advantage + intercept
        expected_lam = np.exp(model.mu0_ + model.beta_["T01"] + model.gamma_)
        assert lam == pytest.approx(expected_lam)

    def test_fallback_to_independent_poisson_on_non_convergence(self):
        matches = generate_synthetic_matches()
        # max_iter=1 makes convergence within budget essentially impossible
        model = DixonColesModel(min_matches=15, max_iter=1).fit(matches, xi=0.0065)
        assert model.fallback_used_ is True
        assert model.rho_ == 0.0

    def test_sparse_team_gets_a_real_but_shrunk_rating_not_a_hard_zero(self):
        matches = generate_synthetic_matches(n_teams=10, rounds=6)
        rare_team_rows = matches.iloc[:3].copy()
        rare_team_rows["home_team"] = "RARE"
        matches = pd.concat([matches, rare_team_rows], ignore_index=True)

        model = DixonColesModel(min_matches=15).fit(matches, xi=0.0065)

        assert "RARE" in model.regularized_teams_
        # Smooth shrinkage (WP2): a sparse team is pulled hard toward the
        # league mean (0, the sum-to-zero basis) but is not hard-cut to
        # exactly 0.0 like the old min_matches on/off gate.
        assert model.alpha_["RARE"] != pytest.approx(0.0)
        assert abs(model.alpha_["RARE"]) < 0.5

    def test_shrinkage_penalty_coefficient_scales_inversely_with_match_count(self):
        # Direct test of the smooth-shrinkage mechanism itself (rather
        # than an emergent full-fit outcome, which a strong enough
        # likelihood signal can swamp): the penalty term added to the
        # negative log-likelihood must be min_matches/n_i per free team,
        # applied to (alpha_i^2 + beta_i^2) — i.e. continuous in n_i, no
        # on/off threshold at min_matches.
        model = DixonColesModel(min_matches=15)
        n_teams = 2
        n_seasons = 1
        a = np.array([1.0, 1.0])  # identical alpha for both free teams
        b = np.array([0.0, 0.0])
        params = np.concatenate([[0.0], [0.2], a, b])  # mu0, gamma, alpha, beta
        home_idx = np.array([0])
        away_idx = np.array([1])
        hg = np.array([1.0])
        ag = np.array([1.0])
        log_fact = np.array([0.0])
        weights = np.array([1.0])
        season_idx = np.array([0])

        def nll(match_counts):
            return model._negative_log_likelihood(
                params, home_idx, away_idx, season_idx, hg, ag, log_fact, log_fact,
                weights, n_teams, n_seasons, rho=0.0, match_counts=match_counts,
            )

        # Holding everything else fixed, only the free teams' match
        # counts change -> only the penalty term should differ.
        penalty_few = nll(np.array([3.0, 3.0])) - nll(np.array([1e9, 1e9]))
        penalty_many = nll(np.array([300.0, 300.0])) - nll(np.array([1e9, 1e9]))

        assert penalty_few > penalty_many > 0
        # min_matches / n_i * 0.5 * sum(alpha_i^2 + beta_i^2), alpha=1, beta=0 for both teams
        assert penalty_few == pytest.approx(2 * 0.5 * (15 / 3) * 1.0, rel=1e-6)
        assert penalty_many == pytest.approx(2 * 0.5 * (15 / 300) * 1.0, rel=1e-6)

    def test_sparse_team_shrinks_harder_than_well_observed_team_in_a_full_fit(self):
        # A moderate (not saturating) attack signal: enough for the
        # likelihood to identify a real difference from league average,
        # but not so extreme that it swamps the shrinkage penalty.
        rng = np.random.default_rng(3)
        rows = []
        start = pd.Timestamp("2023-08-01")
        day = 0
        weak_opponents = [f"WEAK{i:02d}" for i in range(6)]
        for opp in weak_opponents:
            for strong, n_reps in (("FEW", 2), ("MANY", 40)):
                for _ in range(n_reps):
                    rows.append({
                        "date": start + pd.Timedelta(days=day), "league": "E0", "season": "2324",
                        "home_team": strong, "away_team": opp,
                        "home_goals": int(rng.poisson(2.2)), "away_goals": int(rng.poisson(1.0)),
                        "result": "H",
                    })
                    day += 1
            rows.append({
                "date": start + pd.Timedelta(days=day), "league": "E0", "season": "2324",
                "home_team": opp, "away_team": weak_opponents[(weak_opponents.index(opp) + 1) % 6],
                "home_goals": int(rng.poisson(1.0)), "away_goals": int(rng.poisson(1.0)), "result": "D",
            })
            day += 1
        matches = pd.DataFrame(rows)

        model = DixonColesModel(min_matches=15, max_iter=1000).fit(matches, xi=0.0065)

        assert abs(model.alpha_["FEW"]) < abs(model.alpha_["MANY"])

    def test_fit_on_alternate_goal_columns(self):
        """goal_columns lets the optimizer fit on e.g. blended xG instead
        of raw goals (src/ingestion/sources.py), without touching the
        default (home_goals/away_goals) behavior."""
        matches = generate_synthetic_matches()
        rng = np.random.default_rng(7)
        # A noisy-but-correlated xG proxy for the actual goals scored.
        matches["home_xg"] = (matches["home_goals"] + rng.normal(0, 0.3, len(matches))).clip(lower=0)
        matches["away_xg"] = (matches["away_goals"] + rng.normal(0, 0.3, len(matches))).clip(lower=0)

        model = DixonColesModel(min_matches=15).fit(
            matches, xi=0.0065, goal_columns=("home_xg", "away_xg"),
        )

        assert model.converged_ is True
        lam, mu, rho = model.predict("T00", "T01")
        assert lam > 0 and mu > 0

    def test_fit_rejects_missing_goal_columns(self):
        matches = generate_synthetic_matches()
        with pytest.raises(ValueError):
            DixonColesModel(min_matches=15).fit(matches, goal_columns=("home_xg", "away_xg"))

    def test_fit_drops_rows_with_null_goal_column(self):
        matches = generate_synthetic_matches()
        matches["home_xg"] = matches["home_goals"].astype(float)
        matches["away_xg"] = matches["away_goals"].astype(float)
        # Blank out xG for a chunk of rows, as a left-joined blend would
        # for matches a supplemental source doesn't cover.
        matches.loc[matches.index[:50], ["home_xg", "away_xg"]] = np.nan

        model = DixonColesModel(min_matches=15).fit(
            matches, xi=0.0065, goal_columns=("home_xg", "away_xg"),
        )
        assert model.converged_ is True

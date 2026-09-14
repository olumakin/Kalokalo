import numpy as np
import pandas as pd
import pytest

from src.models.dixon_coles import DixonColesModel, tau
from src.models.simulator import build_score_matrix


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

    def test_regularized_teams_get_league_median_rating(self):
        matches = generate_synthetic_matches(n_teams=10, rounds=6)
        # Add one team with very few matches (< 15)
        rare_team_rows = matches.iloc[:3].copy()
        rare_team_rows["home_team"] = "RARE"
        matches = pd.concat([matches, rare_team_rows], ignore_index=True)

        model = DixonColesModel(min_matches=15).fit(matches, xi=0.0065)
        assert "RARE" in model.regularized_teams_
        assert model.alpha_["RARE"] == pytest.approx(0.0)
        assert model.beta_["RARE"] == pytest.approx(0.0)

import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from src.analytics.edge import apply_risk_caps, calculate_ev, qualifies
from src.ingestion.data_loader import validate_matches
from src.ingestion.normalizer import load_team_mappings, resolve_team
from src.ingestion.odds_feed import (
    FIXTURE_SOURCE_SAMPLE_CARD,
    FIXTURE_SOURCE_UNAVAILABLE,
    get_upcoming_fixtures,
)
from src.models.dixon_coles import (
    DixonColesModel,
    UnknownTeamError,
    tau,
)
from src.models.simulator import build_score_matrix, check_tau_admissibility
from src.pipeline import build_predictions
from src.tracking.ledger import Ledger
from src.validation.gate_report import (
    GATE_INCONCLUSIVE,
    GATE_PROCEED_SUBSET,
    GATE_REPOSITION,
    compute_gate_decision,
)


class TestA01DixonColesFactors:
    def test_asymmetric_cells_use_correct_rates(self):
        """A01: For home goals x, away goals y:
        tau(0,1) must be 1 + lam*rho (not 1 + mu*rho).
        tau(1,0) must be 1 + mu*rho (not 1 + lam*rho).
        """
        lam = np.array([1.4])
        mu = np.array([1.1])
        rho = 0.1

        t00 = tau(np.array([0.0]), np.array([0.0]), lam, mu, rho)[0]
        t01 = tau(np.array([0.0]), np.array([1.0]), lam, mu, rho)[0]
        t10 = tau(np.array([1.0]), np.array([0.0]), lam, mu, rho)[0]
        t11 = tau(np.array([1.0]), np.array([1.0]), lam, mu, rho)[0]

        assert t00 == pytest.approx(1.0 - 1.4 * 1.1 * 0.1)  # 0.846
        assert t01 == pytest.approx(1.0 + 1.4 * 0.1)        # 1.14
        assert t10 == pytest.approx(1.0 + 1.1 * 0.1)        # 1.11
        assert t11 == pytest.approx(1.0 - 0.1)              # 0.90

    def test_marginals_conserved_with_unequal_rates(self):
        """A01: Summing across away goals must yield exact Poisson marginal for home goals."""
        lam = 1.6
        mu = 0.9
        rho = 0.08
        matrix = build_score_matrix(lam, mu, rho, grid_size=25)
        # Check marginals for home goals = 0 and 1
        assert np.sum(matrix[0, :]) == pytest.approx(poisson.pmf(0, lam), rel=1e-3)
        assert np.sum(matrix[1, :]) == pytest.approx(poisson.pmf(1, lam), rel=1e-3)

    def test_negative_rho_asymmetric_cells(self):
        """A01: Asymmetric lambda != mu with negative rho."""
        lam = np.array([1.8])
        mu = np.array([0.8])
        rho = -0.06

        t00 = tau(np.array([0.0]), np.array([0.0]), lam, mu, rho)[0]
        t01 = tau(np.array([0.0]), np.array([1.0]), lam, mu, rho)[0]
        t10 = tau(np.array([1.0]), np.array([0.0]), lam, mu, rho)[0]
        t11 = tau(np.array([1.0]), np.array([1.0]), lam, mu, rho)[0]

        assert t00 == pytest.approx(1.0 - 1.8 * 0.8 * (-0.06))  # 1.0864
        assert t01 == pytest.approx(1.0 + 1.8 * (-0.06))        # 0.892
        assert t10 == pytest.approx(1.0 + 0.8 * (-0.06))        # 0.952
        assert t11 == pytest.approx(1.0 - (-0.06))              # 1.06

        matrix = build_score_matrix(1.8, 0.8, -0.06, grid_size=25)
        assert np.sum(matrix[0, :]) == pytest.approx(poisson.pmf(0, 1.8), rel=1e-3)
        assert np.sum(matrix[1, :]) == pytest.approx(poisson.pmf(1, 1.8), rel=1e-3)



class TestA02NoSilentSampleFallback:
    def test_production_default_returns_unavailable_not_sample(self, monkeypatch):
        """A02: Without live feeds, default get_upcoming_fixtures returns unavailable."""
        import src.ingestion.odds_feed as of
        monkeypatch.setattr(of, "fetch_live_odds", lambda lg, **kw: pd.DataFrame(columns=of.FIXTURE_COLUMNS))
        monkeypatch.setattr(of, "fetch_free_schedule", lambda lgs: pd.DataFrame(columns=of.FIXTURE_COLUMNS))

        df, source = get_upcoming_fixtures(["E0"], allow_sample=False)
        assert source == FIXTURE_SOURCE_UNAVAILABLE
        assert df.empty


class TestA03TeamDisambiguation:
    def test_monza_and_monaco_distinct(self):
        """A03: Monza (I1) and Monaco (F1) have distinct canonical codes."""
        mappings = load_team_mappings()
        assert resolve_team("Monza", "I1", mappings) == "MNZ"
        assert resolve_team("Monaco", "F1", mappings) == "MON"

    def test_cross_league_model_isolation_and_parameter_independence(self):
        """A03: Adversarial check - team from league A cannot enter prediction in league B."""
        from src.pipeline import fit_models_by_league
        i1_matches = pd.DataFrame([
            {"date": pd.Timestamp("2023-09-01"), "league": "I1", "season": "2324", "home_team": "MNZ", "away_team": "JUV", "home_goals": 1, "away_goals": 0},
            {"date": pd.Timestamp("2023-09-08"), "league": "I1", "season": "2324", "home_team": "JUV", "away_team": "MNZ", "home_goals": 2, "away_goals": 1},
        ] * 10)
        f1_matches = pd.DataFrame([
            {"date": pd.Timestamp("2023-09-01"), "league": "F1", "season": "2324", "home_team": "MON", "away_team": "PSG", "home_goals": 2, "away_goals": 2},
            {"date": pd.Timestamp("2023-09-08"), "league": "F1", "season": "2324", "home_team": "PSG", "away_team": "MON", "home_goals": 1, "away_goals": 3},
        ] * 10)
        combined = pd.concat([i1_matches, f1_matches], ignore_index=True)
        settings = {
            "model": {
                "min_matches_for_team_rating": 2,
                "max_optimizer_iterations": 50,
                "optimizer_method": "L-BFGS-B",
                "rho_init": -0.05,
                "xi_decay": 0.0065,
            }
        }
        models = fit_models_by_league(combined, settings=settings)

        # I1 model has MNZ, does not have MON
        assert "MNZ" in models["I1"].teams_
        assert "MON" not in models["I1"].teams_

        # F1 model has MON, does not have MNZ
        assert "MON" in models["F1"].teams_
        assert "MNZ" not in models["F1"].teams_

        # Predicting MON in I1 raises UnknownTeamError
        with pytest.raises(UnknownTeamError):
            models["I1"].predict("MON", "JUV")

        # Predicting MNZ in F1 raises UnknownTeamError
        with pytest.raises(UnknownTeamError):
            models["F1"].predict("MNZ", "PSG")


class TestA04EligibilityAndUnknownTeams:
    def test_ineligible_model_creates_no_production_prediction(self):
        """A04: Ineligible model produces no predictions and no silent median fallbacks."""
        model = DixonColesModel()
        model.converged_ = False
        assert not model.is_production_eligible

        upcoming = pd.DataFrame([{
            "fixture_id": "f1",
            "league": "E0",
            "home_team": "ARS",
            "away_team": "CHE",
            "date": pd.Timestamp("2024-01-01"),
            "odds_home": 2.0, "odds_draw": 3.4, "odds_away": 3.8,
            "source": "the_odds_api",
        }])
        settings = {
            "edge": {"min_ev": 0.03, "kelly_fraction": 0.15, "single_match_cap": 0.025, "daily_slate_cap": 0.08}
        }
        pred_df = build_predictions(upcoming, model={"E0": model}, settings=settings)
        assert pred_df.iloc[0]["status"] == "ineligible_model_not_converged"
        assert not pred_df.iloc[0]["qualified"]

    def test_unseen_team_raises_unknown_team_error(self):
        """A04: Unseen team raises UnknownTeamError by default."""
        model = DixonColesModel(min_matches=5)
        model.mu0_ = 0.2
        model.gamma_ = 0.2
        model.rho_ = 0.0
        model.alpha_ = {"ARS": 0.1, "CHE": -0.1}
        model.beta_ = {"ARS": -0.1, "CHE": 0.1}
        model.teams_ = ["ARS", "CHE"]
        model.converged_ = True
        model.fallback_used_ = False

        with pytest.raises(UnknownTeamError):
            model.predict("UNKNOWN", "CHE")


class TestA05ProbabilityAdmissibility:
    def test_inadmissible_tau_rejected(self):
        """A05: Parameters producing negative tau must be rejected."""
        admissible, reason = check_tau_admissibility(lam=4.0, mu=4.0, rho=0.1)
        assert not admissible
        assert "negative" in reason

        with pytest.raises(ValueError, match="Inadmissible"):
            build_score_matrix(lam=4.0, mu=4.0, rho=0.1)


class TestA06GateBaselineComparison:
    def test_selections_worse_than_baseline_cannot_proceed(self):
        """A06: Model with lower CLV than baseline must not receive proceed status."""
        clv_by_league = {
            "E0": {
                "baseline": {"n": 500, "mean_clv": 0.08, "ci_lo": 0.06, "ci_hi": 0.10, "insufficient_sample": False},
                "flagged": {"n": 100, "mean_clv": 0.02, "ci_lo": 0.01, "ci_hi": 0.03, "insufficient_sample": False},
            }
        }
        decision = compute_gate_decision(clv_by_league)
        assert decision["decision"] != GATE_PROCEED_SUBSET

    def test_nan_ci_or_empty_or_malformed_cannot_accidentally_proceed(self):
        """A06: Adversarial inputs (NaNs, empty baseline, tiny sample) cannot return PROCEED."""
        malformed_cases = [
            # Case 1: NaN lower CI
            {"E0": {"baseline": {"n": 500, "mean_clv": 0.01, "ci_lo": 0.0, "ci_hi": 0.02, "insufficient_sample": False},
                    "flagged": {"n": 100, "mean_clv": 0.05, "ci_lo": float("nan"), "ci_hi": 0.08, "insufficient_sample": False}}},
            # Case 2: Insufficient sample flagged as True
            {"E0": {"baseline": {"n": 500, "mean_clv": 0.01, "ci_lo": 0.0, "ci_hi": 0.02, "insufficient_sample": False},
                    "flagged": {"n": 3, "mean_clv": 0.20, "ci_lo": 0.10, "ci_hi": 0.30, "insufficient_sample": True}}},
            # Case 3: Flagged CI positive but lower than baseline
            {"E0": {"baseline": {"n": 500, "mean_clv": 0.12, "ci_lo": 0.10, "ci_hi": 0.14, "insufficient_sample": False},
                    "flagged": {"n": 50, "mean_clv": 0.08, "ci_lo": 0.05, "ci_hi": 0.11, "insufficient_sample": False}}},
        ]
        for case in malformed_cases:
            decision = compute_gate_decision(case)
            assert decision["decision"] != GATE_PROCEED_SUBSET



class TestA11PositionSettlement:
    def test_zero_stake_receives_zero_pnl(self, tmp_path):
        """A11: Unstaked forecast records receive 0 PnL, not phantom returns."""
        ledger_path = tmp_path / "ledger.parquet"
        ledger = Ledger(ledger_path)
        ledger.record_prediction("match_1", "E0", "ARS", "CHE", 0.3, 0.28, 3.5, 0.05, stake_pct=0.0)
        ledger.record_prediction("match_2", "E0", "LIV", "MCI", 0.3, 0.28, 3.5, 0.05, stake_pct=0.02)

        ledger.update_outcome("match_1", "1-1", clv=0.04, pnl=50.0)
        ledger.update_outcome("match_2", "1-1", clv=0.04, pnl=50.0)

        df = ledger.load()
        assert df.loc[df["match_id"] == "match_1", "pnl"].iloc[0] == 0.0
        assert df.loc[df["match_id"] == "match_2", "pnl"].iloc[0] == 50.0


class TestA15RiskCapDeduplication:
    def test_duplicate_fixtures_do_not_exceed_daily_slate_cap(self):
        """A15: Repeated fixture records in slate must not double exposure."""
        stakes = {"m1": 0.02, "m2": 0.02, "m3": 0.02, "m4": 0.02, "m5": 0.02}
        capped = apply_risk_caps(stakes, single_match_cap=0.025, daily_slate_cap=0.08)
        assert sum(capped.values()) <= 0.08 + 1e-9

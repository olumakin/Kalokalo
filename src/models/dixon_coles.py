"""
Dixon-Coles bivariate Poisson goal-scoring model with time decay.

Fits attack strength (alpha), defense weakness (beta), home advantage
(gamma), and the low-score correlation parameter (rho) by maximizing a
time-weighted log-likelihood over historical results. See PID section
3.1-3.3 for the exact parameterization this module implements.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

logger = logging.getLogger(__name__)


def tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score dependence adjustment tau(x, y)."""
    out = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    out = np.where(m00, 1.0 - lam * mu * rho, out)
    out = np.where(m01, 1.0 + mu * rho, out)
    out = np.where(m10, 1.0 + lam * rho, out)
    out = np.where(m11, 1.0 - rho, out)
    return out


class DixonColesModel:
    """Rolling, time-decayed Dixon-Coles model.

    Attributes populated by `fit`:
        mu0_, gamma_, rho_ : float
        alpha_, beta_ : dict[str, float]  (canonical team code -> rating)
        teams_ : list[str]
        reference_team_ : str
        regularized_teams_ : list[str]  (teams defaulted to league median)
        converged_ : bool
        fallback_used_ : bool  (True if rho was forced to 0 after non-convergence)
    """

    def __init__(
        self,
        min_matches: int = 15,
        max_iter: int = 500,
        method: str = "L-BFGS-B",
        rho_init: float = -0.05,
        rho_bounds: tuple[float, float] = (-0.3, 0.3),
    ):
        self.min_matches = min_matches
        self.max_iter = max_iter
        self.method = method
        self.rho_init = rho_init
        self.rho_bounds = rho_bounds

        self.mu0_: float | None = None
        self.gamma_: float | None = None
        self.rho_: float | None = None
        self.alpha_: dict[str, float] = {}
        self.beta_: dict[str, float] = {}
        self.teams_: list[str] = []
        self.reference_team_: str | None = None
        self.regularized_teams_: list[str] = []
        self.converged_: bool | None = None
        self.fallback_used_: bool = False

    # -- internal helpers -------------------------------------------------

    def _negative_log_likelihood(self, params, home_idx, away_idx, hg, ag,
                                  log_fact_h, log_fact_a, weights, n, include_rho):
        mu0 = params[0]
        gamma = params[1]
        offset = 3 if include_rho else 2
        rho = params[2] if include_rho else 0.0
        a = params[offset:offset + n]
        b = params[offset + n:offset + 2 * n]

        alpha_home = np.zeros(len(home_idx))
        beta_home = np.zeros(len(home_idx))
        alpha_away = np.zeros(len(home_idx))
        beta_away = np.zeros(len(home_idx))

        hmask = home_idx >= 0
        amask = away_idx >= 0
        alpha_home[hmask] = a[home_idx[hmask]]
        beta_home[hmask] = b[home_idx[hmask]]
        alpha_away[amask] = a[away_idx[amask]]
        beta_away[amask] = b[away_idx[amask]]

        log_lam = mu0 + alpha_home + beta_away + gamma
        log_mu = mu0 + alpha_away + beta_home
        lam = np.exp(log_lam)
        mu = np.exp(log_mu)

        tau_vals = tau(hg, ag, lam, mu, rho)
        tau_vals = np.clip(tau_vals, 1e-10, None)

        loglik = (
            np.log(tau_vals)
            + hg * log_lam - lam - log_fact_h
            + ag * log_mu - mu - log_fact_a
        )
        return -np.sum(weights * loglik)

    # -- public API ---------------------------------------------------------

    def fit(self, matches: pd.DataFrame, as_of: pd.Timestamp | None = None,
            xi: float = 0.0065) -> "DixonColesModel":
        """Fit the model on matches strictly before `as_of` (or all rows if
        `as_of` is None). `matches` must already be in canonical schema
        (see src.ingestion.normalizer) with a `date` column of Timestamps.
        """
        df = matches.copy()
        if as_of is not None:
            df = df[df["date"] < as_of]
        if df.empty:
            raise ValueError("No matches available to fit on")

        cutoff = as_of if as_of is not None else (df["date"].max() + pd.Timedelta(days=1))
        delta_days = (cutoff - df["date"]).dt.days.clip(lower=0).to_numpy(dtype=float)
        weights = np.exp(-xi * delta_days)

        counts = pd.concat([df["home_team"], df["away_team"]]).value_counts()
        all_teams = sorted(counts.index.tolist())
        eligible = [t for t in all_teams if counts[t] >= self.min_matches]
        regularized = [t for t in all_teams if t not in eligible]

        if not eligible:
            raise ValueError(
                f"No team has >= {self.min_matches} matches; cannot fit Dixon-Coles model"
            )

        reference_team = counts.loc[eligible].idxmax()
        free_teams = [t for t in eligible if t != reference_team]
        team_index = {t: i for i, t in enumerate(free_teams)}
        n = len(free_teams)

        home_idx = df["home_team"].map(lambda t: team_index.get(t, -1)).to_numpy()
        away_idx = df["away_team"].map(lambda t: team_index.get(t, -1)).to_numpy()
        hg = df["home_goals"].to_numpy(dtype=float)
        ag = df["away_goals"].to_numpy(dtype=float)
        log_fact_h = gammaln(hg + 1)
        log_fact_a = gammaln(ag + 1)

        avg_goals = float(np.average(np.concatenate([hg, ag]), weights=np.concatenate([weights, weights])))
        mu0_init = np.log(max(avg_goals, 0.1))

        def run(include_rho: bool):
            offset = 3 if include_rho else 2
            x0 = np.zeros(offset + 2 * n)
            x0[0] = mu0_init
            x0[1] = 0.2
            if include_rho:
                x0[2] = self.rho_init

            bounds = [(None, None)] * len(x0)
            if include_rho:
                bounds[2] = self.rho_bounds

            res = minimize(
                self._negative_log_likelihood,
                x0,
                args=(home_idx, away_idx, hg, ag, log_fact_h, log_fact_a, weights, n, include_rho),
                method=self.method,
                bounds=bounds,
                options={"maxiter": self.max_iter},
            )
            return res, offset

        res, offset = run(include_rho=True)
        fallback_used = False
        if not res.success:
            logger.warning(
                "Dixon-Coles optimizer failed to converge with rho free (%s); "
                "falling back to independent Poisson (rho=0)",
                res.message,
            )
            res, offset = run(include_rho=False)
            fallback_used = True
            if not res.success:
                logger.warning(
                    "Dixon-Coles fallback (rho=0) also failed to converge within "
                    "%d iterations: %s", self.max_iter, res.message,
                )

        params = res.x
        mu0 = params[0]
        gamma = params[1]
        rho = 0.0 if fallback_used else params[2]
        a = params[offset:offset + n]
        b = params[offset + n:offset + 2 * n]

        # Sum-to-zero identifiability is enforced over the *eligible*
        # (optimized + reference) teams only. Regularized teams are pinned
        # at exactly the league median (0, 0) by definition and must not
        # be shifted by that renormalization.
        alpha_eligible = {t: 0.0 for t in eligible}
        beta_eligible = {t: 0.0 for t in eligible}
        for t, i in team_index.items():
            alpha_eligible[t] = float(a[i])
            beta_eligible[t] = float(b[i])

        mean_alpha = float(np.mean(list(alpha_eligible.values())))
        mean_beta = float(np.mean(list(beta_eligible.values())))

        self.alpha_ = {t: alpha_eligible[t] - mean_alpha for t in eligible}
        self.beta_ = {t: beta_eligible[t] - mean_beta for t in eligible}
        for t in regularized:
            self.alpha_[t] = 0.0
            self.beta_[t] = 0.0
        self.mu0_ = float(mu0 + mean_alpha + mean_beta)
        self.gamma_ = float(gamma)
        self.rho_ = float(rho)
        self.teams_ = all_teams
        self.reference_team_ = reference_team
        self.regularized_teams_ = regularized
        self.converged_ = bool(res.success)
        self.fallback_used_ = fallback_used

        return self

    def predict(self, home_team: str, away_team: str) -> tuple[float, float, float]:
        """Return (lambda, mu, rho) expected-goals parameters for a fixture.

        Teams unseen during fitting default to league-median rating (0, 0),
        per the promoted/new-team regularization rule.
        """
        if self.mu0_ is None:
            raise RuntimeError("Model has not been fit yet")

        alpha_h = self.alpha_.get(home_team, 0.0)
        beta_h = self.beta_.get(home_team, 0.0)
        alpha_a = self.alpha_.get(away_team, 0.0)
        beta_a = self.beta_.get(away_team, 0.0)

        if home_team not in self.teams_:
            logger.warning("Unseen team %r; defaulting to league-median rating", home_team)
        if away_team not in self.teams_:
            logger.warning("Unseen team %r; defaulting to league-median rating", away_team)

        lam = float(np.exp(self.mu0_ + alpha_h + beta_a + self.gamma_))
        mu = float(np.exp(self.mu0_ + alpha_a + beta_h))
        return lam, mu, self.rho_

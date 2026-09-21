"""
Dixon-Coles bivariate Poisson goal-scoring model with time decay.

Fits attack strength (alpha), defense weakness (beta), a season-aware
home advantage (gamma), and the low-score correlation parameter (rho)
by maximizing a time-weighted log-likelihood over historical results.
See PID section 3.1-3.3 for the exact parameterization this module
implements.

WP2 corrections over the original single-gamma / hard-cutoff / joint-rho
fit:

- N-1 reparameterization: one well-observed reference team is pinned at
  (alpha=0, beta=0) during optimization (standard reference-category
  identifiability for the attack/defense ratings), then every team's
  published rating is re-centered onto the sum-to-zero basis via a
  mean-shift absorbed into mu0 — a pure change of basis that leaves
  every fitted lambda/mu exactly unchanged (see the recentering step in
  `fit` for the algebra).
- Per-season gamma: home advantage is fit as one value per season
  present in the training window rather than a single constant, since
  it demonstrably drifts (e.g. behind-closed-doors matches). `predict`
  uses the most recently fitted season's value as the best available
  estimate for an upcoming, not-yet-played season; `gamma_by_season_`
  exposes the full history for diagnostics.
- Smooth shrinkage: a team's attack/defense rating is penalized toward
  the league mean by a continuous factor (min_matches / n_matches)
  rather than being hard-cut to exactly (0, 0) below a threshold — a
  team with 14 matches and one with 16 no longer get discontinuously
  different treatment.
- Profiled rho: rho is optimized in an outer 1-D bounded search over
  the *profile* negative log-likelihood (mu0/gamma/alpha/beta
  re-optimized at each candidate rho), rather than jointly with every
  other parameter in one high-dimensional optimization. rho is a weak,
  narrow-support nuisance parameter (it only touches the tau adjustment
  on four low-score cells) that destabilizes a joint fit; profiling it
  out is the standard fix and is what Dixon & Coles (1997) do.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import gammaln

logger = logging.getLogger(__name__)

class UnknownTeamError(ValueError):
    """Raised when inference is requested for a team unseen during fitting (A04)."""


class FitConvergenceError(RuntimeError):
    """Raised when model fitting fails to converge (A04)."""


# Cap on how many candidate rho values the outer profile search evaluates.
# Each one re-runs the full inner (mu0, gamma, alpha, beta) optimization,
# so this bounds the multiplier on total fit cost; rho itself is a weak,
# narrow-support parameter that doesn't reward more than ~15 evaluations
# of precision (see PROFILE_RHO_XATOL below).
PROFILE_RHO_MAX_ITER = 15
PROFILE_RHO_XATOL = 1e-3


def tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score dependence adjustment tau(x, y)."""
    out = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    out = np.where(m00, 1.0 - lam * mu * rho, out)
    out = np.where(m01, 1.0 + lam * rho, out)
    out = np.where(m10, 1.0 + mu * rho, out)
    out = np.where(m11, 1.0 - rho, out)
    return out


class DixonColesModel:
    """Rolling, time-decayed Dixon-Coles model.

    Attributes populated by `fit`:
        mu0_, rho_ : float
        gamma_ : float  (most recently fitted season's home advantage)
        gamma_by_season_ : dict[str, float]  (every fitted season's gamma)
        alpha_, beta_ : dict[str, float]  (canonical team code -> rating,
            smoothly shrunk toward 0 for teams with few matches)
        teams_ : list[str]
        reference_team_ : str
        regularized_teams_ : list[str]  (teams with fewer than
            `min_matches`, kept only as a diagnostic list now — see
            module docstring's "Smooth shrinkage": these teams still get
            a real fitted, shrunk rating, not a hardcoded 0)
        converged_ : bool
        fallback_used_ : bool  (True if rho was forced to 0 after the fit
            at the profiled-optimal rho failed to converge)
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
        self.gamma_by_season_: dict[str, float] = {}
        self.rho_: float | None = None
        self.alpha_: dict[str, float] = {}
        self.beta_: dict[str, float] = {}
        self.teams_: list[str] = []
        self.reference_team_: str | None = None
        self.regularized_teams_: list[str] = []
        self.converged_: bool | None = None
        self.fallback_used_: bool = False

    # -- internal helpers -------------------------------------------------

    def _negative_log_likelihood(self, params, home_idx, away_idx, season_idx, hg, ag,
                                  log_fact_h, log_fact_a, weights, n_teams, n_seasons,
                                  rho, match_counts):
        mu0 = params[0]
        gamma = params[1:1 + n_seasons]
        a = params[1 + n_seasons: 1 + n_seasons + n_teams]
        b = params[1 + n_seasons + n_teams: 1 + n_seasons + 2 * n_teams]

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

        gamma_row = gamma[season_idx]

        log_lam = mu0 + alpha_home + beta_away + gamma_row
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
        nll = -np.sum(weights * loglik)

        # Smooth shrinkage: each free team's rating is ridge-penalized
        # toward the league mean (0, already the sum-to-zero basis) by a
        # weight that scales continuously with how little data it has,
        # instead of the old hard min_matches on/off cutoff.
        shrink_penalty = 0.5 * np.sum((self.min_matches / match_counts) * (a ** 2 + b ** 2))

        return nll + shrink_penalty

    # -- public API ---------------------------------------------------------

    def fit(self, matches: pd.DataFrame, as_of: pd.Timestamp | None = None,
            xi: float = 0.0065, goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
            warm_start: dict[str, tuple[float, float]] | None = None) -> "DixonColesModel":
        """Fit the model on matches strictly before `as_of` (or all rows if
        `as_of` is None). `matches` must already be in canonical schema
        (see src.ingestion.normalizer) with a `date` column of Timestamps.

        `goal_columns` defaults to the actual final-score columns. Pass
        e.g. `("home_xg", "away_xg")` to fit on blended Expected Goals
        (src/ingestion/sources.py) instead of raw goals scored — a lower-
        variance proxy for attacking/defensive quality. This relies on
        the Poisson log-likelihood's gamma-function generalization of x!
        to non-integer x, a documented but approximate technique (xG
        isn't literally Poisson-distributed count data); it is not the
        PID's default and must be opted into explicitly.

        `warm_start`, if given, maps team code -> (alpha, beta) from a
        prior fit (e.g. last matchweek's `self.alpha_`/`self.beta_`
        zipped by team), used as the initial guess for the optimizer
        instead of 0.0 — keyed by team ID rather than positionally, so
        it stays correct across a season boundary where the team set
        changes (promotion/relegation): a team absent from `warm_start`
        (newly promoted, or simply new to this repo's history) starts
        from (0.0, 0.0) — the same promoted-team prior smooth shrinkage
        already pulls sparse teams toward, so a missing warm-start entry
        is never a special case to handle, just the ordinary default.
        """
        goal_col_h, goal_col_a = goal_columns
        df = matches.copy()
        if as_of is not None:
            df = df[df["date"] < as_of]
        if df.empty:
            raise ValueError("No matches available to fit on")
        if goal_col_h not in df.columns or goal_col_a not in df.columns:
            raise ValueError(f"goal_columns {goal_columns} not present in matches")
        df = df.dropna(subset=[goal_col_h, goal_col_a])
        if df.empty:
            raise ValueError(f"No matches with non-null {goal_columns} to fit on")

        cutoff = as_of if as_of is not None else (df["date"].max() + pd.Timedelta(days=1))
        delta_days = (cutoff - df["date"]).dt.days.clip(lower=0).to_numpy(dtype=float)
        weights = np.exp(-xi * delta_days)

        counts = pd.concat([df["home_team"], df["away_team"]]).value_counts()
        all_teams = sorted(counts.index.tolist())
        if not all_teams:
            raise ValueError("No teams found in matches; cannot fit Dixon-Coles model")
        regularized = [t for t in all_teams if counts[t] < self.min_matches]

        # N-1 reparameterization: the most-observed team is pinned at
        # (alpha=0, beta=0) for identifiability during optimization.
        # Every other team gets a free parameter (including sparse ones —
        # they are only *penalized*, via the shrinkage term below, not
        # excluded outright).
        reference_team = counts.idxmax()
        free_teams = [t for t in all_teams if t != reference_team]
        team_index = {t: i for i, t in enumerate(free_teams)}
        n = len(free_teams)
        match_counts = np.array([float(counts[t]) for t in free_teams])

        home_idx = df["home_team"].map(lambda t: team_index.get(t, -1)).to_numpy()
        away_idx = df["away_team"].map(lambda t: team_index.get(t, -1)).to_numpy()
        hg = df[goal_col_h].to_numpy(dtype=float)
        ag = df[goal_col_a].to_numpy(dtype=float)
        log_fact_h = gammaln(hg + 1)
        log_fact_a = gammaln(ag + 1)

        # Per-season gamma: one home-advantage parameter per season
        # present in the training window, rather than a single constant.
        season_values = df["season"] if "season" in df.columns else pd.Series("_all_", index=df.index)
        season_list = sorted(season_values.astype(str).unique().tolist())
        season_to_idx = {s: i for i, s in enumerate(season_list)}
        season_idx = season_values.astype(str).map(season_to_idx).to_numpy()
        n_seasons = len(season_list)
        current_season = str(season_values.loc[df["date"].idxmax()])

        avg_goals = float(np.average(np.concatenate([hg, ag]), weights=np.concatenate([weights, weights])))
        mu0_init = np.log(max(avg_goals, 0.1))

        x0 = np.zeros(1 + n_seasons + 2 * n)
        x0[0] = mu0_init
        x0[1:1 + n_seasons] = 0.2
        if warm_start:
            for t, i in team_index.items():
                prior = warm_start.get(t)
                if prior is not None:
                    x0[1 + n_seasons + i] = prior[0]
                    x0[1 + n_seasons + n + i] = prior[1]

        def fit_at_rho(rho: float):
            bounds = [(None, None)] * len(x0)
            return minimize(
                self._negative_log_likelihood,
                x0,
                args=(home_idx, away_idx, season_idx, hg, ag, log_fact_h, log_fact_a,
                      weights, n, n_seasons, rho, match_counts),
                method=self.method,
                bounds=bounds,
                options={"maxiter": self.max_iter},
            )

        # Profiled rho: an outer 1-D bounded search over the profile NLL
        # (mu0/gamma/alpha/beta re-optimized at each candidate rho),
        # rather than one joint (rho + everything) optimization — rho is
        # a weak nuisance parameter that destabilizes a joint fit.
        profile_result = minimize_scalar(
            lambda r: fit_at_rho(r).fun,
            bounds=self.rho_bounds,
            method="bounded",
            options={"maxiter": PROFILE_RHO_MAX_ITER, "xatol": PROFILE_RHO_XATOL},
        )
        best_rho = float(np.clip(profile_result.x, *self.rho_bounds))

        res = fit_at_rho(best_rho)
        fallback_used = False
        if not res.success:
            logger.warning(
                "Dixon-Coles fit at profiled rho=%.4f failed to converge (%s); "
                "falling back to independent Poisson (rho=0)",
                best_rho, res.message,
            )
            res = fit_at_rho(0.0)
            best_rho = 0.0
            fallback_used = True
            if not res.success:
                logger.warning(
                    "Dixon-Coles fallback (rho=0) also failed to converge within "
                    "%d iterations: %s", self.max_iter, res.message,
                )

        params = res.x
        mu0 = params[0]
        gamma = params[1:1 + n_seasons]
        rho = 0.0 if fallback_used else best_rho
        a = params[1 + n_seasons: 1 + n_seasons + n]
        b = params[1 + n_seasons + n: 1 + n_seasons + 2 * n]

        # Recenter onto the sum-to-zero basis over *every* team (the
        # reference team's pinned 0 included). This is a pure change of
        # basis: shifting every alpha/beta by -mean and correspondingly
        # shifting mu0 by +mean leaves every fitted log_lam/log_mu, and
        # therefore the likelihood, exactly unchanged — it only makes
        # ratings interpretable relative to the league average rather
        # than to one arbitrary reference team.
        alpha_all = {reference_team: 0.0}
        beta_all = {reference_team: 0.0}
        for t, i in team_index.items():
            alpha_all[t] = float(a[i])
            beta_all[t] = float(b[i])

        mean_alpha = float(np.mean(list(alpha_all.values())))
        mean_beta = float(np.mean(list(beta_all.values())))

        self.alpha_ = {t: alpha_all[t] - mean_alpha for t in all_teams}
        self.beta_ = {t: beta_all[t] - mean_beta for t in all_teams}
        self.mu0_ = float(mu0 + mean_alpha + mean_beta)
        self.gamma_by_season_ = {season_list[i]: float(gamma[i]) for i in range(n_seasons)}
        self.gamma_ = self.gamma_by_season_[current_season]
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
        per the promoted/new-team regularization rule. Home advantage
        uses `gamma_` — the most recently fitted season's value, the best
        available estimate for a fixture in a season not yet played;
        `gamma_by_season_` holds the full per-season history.
        """
    @property
    def is_production_eligible(self) -> bool:
        """Check whether the fitted model meets strict production criteria (A04).

        Requires successful convergence, no rho=0 fallback substitution,
        and finite parameters.
        """
        if self.mu0_ is None or not self.converged_ or self.fallback_used_:
            return False
        return bool(
            np.isfinite(self.mu0_)
            and np.isfinite(self.gamma_)
            and np.isfinite(self.rho_)
            and all(np.isfinite(list(self.alpha_.values())))
            and all(np.isfinite(list(self.beta_.values())))
        )

    def predict(
        self,
        home_team: str,
        away_team: str,
        allow_unseen: bool = False,
    ) -> tuple[float, float, float]:
        """Return (lambda, mu, rho) expected-goals parameters for a fixture.

        By default (allow_unseen=False), raises UnknownTeamError if either
        team was unseen during fitting (A04). If allow_unseen=True is
        explicitly set, defaults to league-median rating (0, 0) with a warning.
        """
        if self.mu0_ is None:
            raise RuntimeError("Model has not been fit yet")

        if not self.converged_:
            raise FitConvergenceError("Model did not converge; predictions are inadmissible")

        unseen = []
        if home_team not in self.teams_:
            unseen.append(home_team)
        if away_team not in self.teams_:
            unseen.append(away_team)

        if unseen:
            if not allow_unseen:
                raise UnknownTeamError(f"Cannot generate prediction for unseen team(s): {', '.join(unseen)}")
            logger.warning("Unseen team(s) %r; defaulting to league-median rating (allow_unseen=True)", unseen)

        alpha_h = self.alpha_.get(home_team, 0.0)
        beta_h = self.beta_.get(home_team, 0.0)
        alpha_a = self.alpha_.get(away_team, 0.0)
        beta_a = self.beta_.get(away_team, 0.0)

        lam = float(np.exp(self.mu0_ + alpha_h + beta_a + self.gamma_))
        mu = float(np.exp(self.mu0_ + alpha_a + beta_h))
        return lam, mu, self.rho_

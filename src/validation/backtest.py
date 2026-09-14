"""
Strict chronological walk-forward validation engine.

For matchday T, the model is trained ONLY on matches played strictly
before T (zero data leakage, PID section 7). Retraining the full
optimizer for every single matchday is expensive, so the model is
refit periodically (`retrain_every_days`); between refits it is still
always the case that the training cutoff is < the date of any match it
is used to predict, so the no-leakage guarantee holds regardless of
retrain cadence.
"""
from __future__ import annotations

import logging

import pandas as pd

from src.analytics.devig import devig
from src.analytics.edge import calculate_ev, qualifies
from src.models.dixon_coles import DixonColesModel
from src.models.simulator import match_probabilities

logger = logging.getLogger(__name__)


class WalkForwardBacktest:
    def __init__(
        self,
        min_matches: int = 15,
        max_iter: int = 500,
        method: str = "L-BFGS-B",
        xi: float = 0.0065,
        rolling_window_days: int = 730,
        retrain_every_days: int = 7,
        devig_method: str = "multiplicative",
        min_ev: float = 0.03,
        min_train_matches: int = 100,
    ):
        self.min_matches = min_matches
        self.max_iter = max_iter
        self.method = method
        self.xi = xi
        self.rolling_window_days = rolling_window_days
        self.retrain_every_days = retrain_every_days
        self.devig_method = devig_method
        self.min_ev = min_ev
        self.min_train_matches = min_train_matches

    def run(self, matches: pd.DataFrame) -> pd.DataFrame:
        """Run the walk-forward simulation and return a per-match ledger of
        model vs. market probabilities, EV, and realized outcomes.
        """
        df = matches.sort_values("date").reset_index(drop=True)
        unique_dates = sorted(df["date"].unique())

        model: DixonColesModel | None = None
        last_train_date: pd.Timestamp | None = None
        results = []

        for current_date in unique_dates:
            needs_retrain = (
                model is None
                or last_train_date is None
                or (current_date - last_train_date).days >= self.retrain_every_days
            )
            if needs_retrain:
                window_start = current_date - pd.Timedelta(days=self.rolling_window_days)
                train_df = df[(df["date"] < current_date) & (df["date"] >= window_start)]
                if len(train_df) >= self.min_train_matches:
                    try:
                        model = DixonColesModel(
                            min_matches=self.min_matches,
                            max_iter=self.max_iter,
                            method=self.method,
                        ).fit(train_df, as_of=current_date, xi=self.xi)
                        last_train_date = current_date
                    except ValueError as exc:
                        logger.warning("Skipping retrain at %s: %s", current_date, exc)
                else:
                    logger.info(
                        "Insufficient training data (%d < %d) as of %s; skipping predictions",
                        len(train_df), self.min_train_matches, current_date,
                    )
                    model = None

            if model is None:
                continue

            day_matches = df[df["date"] == current_date]
            for _, row in day_matches.iterrows():
                if pd.isna(row.get("odds_draw")) or pd.isna(row.get("odds_home")) or pd.isna(row.get("odds_away")):
                    continue

                lam, mu, rho = model.predict(row["home_team"], row["away_team"])
                probs = match_probabilities(lam, mu, rho)
                model_p_draw = probs["p_draw"]

                market_h, market_d, market_a = devig(
                    row["odds_home"], row["odds_draw"], row["odds_away"], method=self.devig_method
                )
                ev = calculate_ev(model_p_draw, row["odds_draw"])
                qualified = qualifies(model_p_draw, market_d, ev, min_ev=self.min_ev)
                actual_draw = bool(row["home_goals"] == row["away_goals"])

                results.append({
                    "date": current_date,
                    "league": row["league"],
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "model_p_draw": model_p_draw,
                    "market_p_draw": market_d,
                    "odds_draw": row["odds_draw"],
                    "ev": ev,
                    "qualified": qualified,
                    "actual_draw": actual_draw,
                    "model_converged": model.converged_,
                    "model_fallback_used": model.fallback_used_,
                })

        return pd.DataFrame(results)

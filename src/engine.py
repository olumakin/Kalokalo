"""
Shared strict domain engine for Draw Value Prediction (M01).

UI-agnostic and storage-agnostic domain services providing model fitting,
fixture scoring, and edge qualification with strict validation.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.analytics.devig import devig
from src.analytics.edge import apply_risk_caps, calculate_ev, kelly_fraction, qualifies
from src.config import AppConfig, get_config
from src.models.dixon_coles import DixonColesModel, UnknownTeamError
from src.models.simulator import match_probabilities, top_scorelines

logger = logging.getLogger(__name__)


class DomainPredictionEngine:
    """Core domain service for DVPE modeling and prediction."""

    def __init__(self, config: AppConfig | None = None):
        self.config = config or get_config()

    def fit_leagues(
        self,
        matches: pd.DataFrame,
        goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
    ) -> dict[str, DixonColesModel]:
        """Fit independent Dixon-Coles models per league (A03)."""
        models = {}
        m_cfg = self.config.model
        for league, league_matches in matches.groupby("league"):
            model = DixonColesModel(
                min_matches=m_cfg.min_matches_for_team_rating,
                max_iter=m_cfg.max_optimizer_iterations,
                method=m_cfg.optimizer_method,
                rho_init=m_cfg.rho_init,
            ).fit(league_matches, xi=m_cfg.xi_decay, goal_columns=goal_columns)

            logger.info(
                "Fitted domain model for %s: mu0=%.4f gamma=%.4f rho=%.4f eligible=%s",
                league, model.mu0_, model.gamma_, model.rho_, model.is_production_eligible,
            )
            models[str(league)] = model
        return models

    def predict_slate(
        self,
        fixtures: pd.DataFrame,
        models: dict[str, DixonColesModel],
    ) -> pd.DataFrame:
        """Score an upcoming fixture card with strict eligibility checks (A04, A15)."""
        if fixtures.empty:
            return pd.DataFrame()

        edge_cfg = self.config.edge
        devig_method = self.config.devig.method

        # Deduplicate fixtures (A15)
        dedup_cols = [c for c in ["date", "league", "home_team", "away_team"] if c in fixtures.columns]
        cleaned_fixtures = fixtures.drop_duplicates(subset=dedup_cols).reset_index(drop=True)

        rows: list[dict[str, Any]] = []
        for _, row in cleaned_fixtures.iterrows():
            league = str(row["league"])
            model = models.get(league)
            match_id = f"{pd.to_datetime(row['date']).date()}_{league}_{row['home_team']}_{row['away_team']}"

            if model is None:
                rows.append(self._ineligible_row(match_id, row, "ineligible_unmodeled_league"))
                continue

            if not model.is_production_eligible:
                rows.append(self._ineligible_row(match_id, row, "ineligible_model_not_converged"))
                continue

            try:
                lam, mu, rho = model.predict(row["home_team"], row["away_team"], allow_unseen=False)
                probs = match_probabilities(lam, mu, rho)
                model_p_draw = probs["p_draw"]
                (h1, a1, p1), (h2, a2, p2) = top_scorelines(probs["matrix"], n=2)

                market_h, market_d, market_a = devig(
                    float(row["odds_home"]), float(row["odds_draw"]), float(row["odds_away"]),
                    method=devig_method,
                )
                ev = calculate_ev(model_p_draw, float(row["odds_draw"]))
                qualified = qualifies(model_p_draw, market_d, ev, min_ev=edge_cfg.min_ev)
                f_kelly = kelly_fraction(model_p_draw, float(row["odds_draw"]), c=edge_cfg.kelly_fraction) if qualified else 0.0

                rows.append({
                    "match_id": match_id,
                    "date": row["date"],
                    "league": league,
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "xg_home": lam,
                    "xg_away": mu,
                    "model_p_home": probs["p_home"],
                    "model_p_draw": model_p_draw,
                    "model_p_away": probs["p_away"],
                    "top_score": f"{h1}-{a1}",
                    "top_score_prob": p1,
                    "alt_score": f"{h2}-{a2}",
                    "alt_score_prob": p2,
                    "market_p_draw": market_d,
                    "odds_draw": float(row["odds_draw"]),
                    "ev": ev,
                    "qualified": qualified,
                    "kelly_raw": f_kelly,
                    "status": "eligible",
                })
            except UnknownTeamError:
                rows.append(self._ineligible_row(match_id, row, "ineligible_unknown_team"))

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        # Apply dual-layer risk caps per calendar day across qualified bets only (A15)
        df["stake_pct"] = 0.0
        qualified_mask = df["qualified"] == True
        if qualified_mask.any():
            for _, day_df in df[qualified_mask].groupby(pd.to_datetime(df["date"]).dt.date):
                stakes = {r["match_id"]: float(r["kelly_raw"]) for _, r in day_df.iterrows()}
                capped = apply_risk_caps(
                    stakes,
                    single_match_cap=edge_cfg.single_match_cap,
                    daily_slate_cap=edge_cfg.daily_slate_cap,
                )
                for match_id, stake in capped.items():
                    df.loc[df["match_id"] == match_id, "stake_pct"] = stake

        return df.sort_values("ev", ascending=False).reset_index(drop=True)

    @staticmethod
    def _ineligible_row(match_id: str, row: pd.Series, status: str) -> dict[str, Any]:
        return {
            "match_id": match_id,
            "date": row["date"],
            "league": row["league"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "xg_home": None,
            "xg_away": None,
            "model_p_home": None,
            "model_p_draw": None,
            "model_p_away": None,
            "top_score": None,
            "top_score_prob": None,
            "alt_score": None,
            "alt_score_prob": None,
            "market_p_draw": None,
            "odds_draw": row.get("odds_draw"),
            "ev": None,
            "qualified": False,
            "kelly_raw": 0.0,
            "status": status,
        }

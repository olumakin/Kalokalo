"""
End-to-end DVPE execution script (PID Phase 5 / Implementation Phases).

    python -m src.pipeline --fixtures data/fixtures/upcoming.csv

Ingests historical Big 5 results, normalizes team identities, fits the
Dixon-Coles model, scores upcoming fixtures against de-vigged market
odds, applies Fractional Kelly sizing under the dual exposure caps, and
records every prediction to the ledger.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

import pandas as pd
import yaml

from src.analytics.devig import devig
from src.analytics.edge import apply_risk_caps, calculate_ev, kelly_fraction, qualifies
from src.ingestion.historical import load_all, load_settings, season_codes
from src.ingestion.normalizer import normalize_dataframe
from src.ingestion.odds_feed import load_fixture_csv
from src.models.dixon_coles import DixonColesModel
from src.models.simulator import match_probabilities
from src.tracking.ledger import Ledger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def build_predictions(fixtures: pd.DataFrame, model: DixonColesModel, settings: dict) -> pd.DataFrame:
    edge_cfg = settings["edge"]
    devig_method = settings["devig"]["method"]

    rows = []
    for _, row in fixtures.iterrows():
        lam, mu, rho = model.predict(row["home_team"], row["away_team"])
        probs = match_probabilities(lam, mu, rho)
        model_p_draw = probs["p_draw"]

        market_h, market_d, market_a = devig(
            row["odds_home"], row["odds_draw"], row["odds_away"], method=devig_method
        )
        ev = calculate_ev(model_p_draw, row["odds_draw"])
        qualified = qualifies(model_p_draw, market_d, ev, min_ev=edge_cfg["min_ev"])
        f_kelly = kelly_fraction(model_p_draw, row["odds_draw"], c=edge_cfg["kelly_fraction"]) if qualified else 0.0

        match_id = f"{row['date'].date()}_{row['league']}_{row['home_team']}_{row['away_team']}"
        rows.append({
            "match_id": match_id,
            "date": row["date"],
            "league": row["league"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "xg_home": lam,
            "xg_away": mu,
            "model_p_home": probs["p_home"],
            "model_p_draw": model_p_draw,
            "model_p_away": probs["p_away"],
            "market_p_draw": market_d,
            "odds_draw": row["odds_draw"],
            "ev": ev,
            "qualified": qualified,
            "kelly_raw": f_kelly,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Apply dual-layer risk caps per calendar day, across qualified bets only.
    df["stake_pct"] = 0.0
    for match_date, day_df in df[df["qualified"]].groupby(df["date"].dt.date):
        stakes = {r["match_id"]: r["kelly_raw"] for _, r in day_df.iterrows()}
        capped = apply_risk_caps(
            stakes,
            single_match_cap=settings["edge"]["single_match_cap"],
            daily_slate_cap=settings["edge"]["daily_slate_cap"],
        )
        for match_id, stake in capped.items():
            df.loc[df["match_id"] == match_id, "stake_pct"] = stake

    return df.sort_values("ev", ascending=False).reset_index(drop=True)


def load_historical_matches(leagues: list[str], seasons_back: int, settings: dict) -> pd.DataFrame:
    """Download (or read from cache) and normalize Big 5 historical results."""
    end_year = date.today().year
    seasons = season_codes(end_year - seasons_back, end_year)
    logger.info("Ingesting historical data for %s / seasons %s", leagues, seasons)
    raw = load_all(leagues, seasons, cache_dir=settings["data_source"]["cache_dir"])
    return normalize_dataframe(raw)


def fit_model(matches: pd.DataFrame, settings: dict) -> DixonColesModel:
    """Fit the Dixon-Coles model on a canonical-schema match history."""
    model_cfg = settings["model"]
    model = DixonColesModel(
        min_matches=model_cfg["min_matches_for_team_rating"],
        max_iter=model_cfg["max_optimizer_iterations"],
        method=model_cfg["optimizer_method"],
        rho_init=model_cfg["rho_init"],
    ).fit(matches, xi=model_cfg["xi_decay"])
    logger.info(
        "Model fit: mu0=%.4f gamma=%.4f rho=%.4f converged=%s fallback_used=%s teams=%d",
        model.mu0_, model.gamma_, model.rho_, model.converged_, model.fallback_used_, len(model.teams_),
    )
    return model


def record_ledger(predictions: pd.DataFrame, settings: dict) -> None:
    if predictions.empty:
        return
    ledger = Ledger(settings["ledger"]["path"], fmt=settings["ledger"]["format"])
    for _, row in predictions.iterrows():
        ledger.record_prediction(
            match_id=row["match_id"],
            league=row["league"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            model_p_draw=row["model_p_draw"],
            market_p_draw=row["market_p_draw"],
            odds_draw=row["odds_draw"],
            ev=row["ev"],
            stake_pct=row["stake_pct"],
            timestamp=pd.Timestamp.utcnow(),
        )


def run(fixtures_path: str, leagues: list[str], seasons_back: int, settings_path: str | None = None) -> pd.DataFrame:
    """CLI entry path: download historical data, fit, score a fixture CSV, log to ledger."""
    settings = load_settings() if settings_path is None else yaml.safe_load(open(settings_path))

    matches = load_historical_matches(leagues, seasons_back, settings)
    if matches.empty:
        logger.error("No historical matches available; cannot fit model. Populate data/historical/ or check network access.")
        return pd.DataFrame()

    model = fit_model(matches, settings)

    fixtures = load_fixture_csv(fixtures_path)
    if fixtures.empty:
        logger.warning("No fixtures loaded from %s", fixtures_path)
        return pd.DataFrame()

    predictions = build_predictions(fixtures, model, settings)
    record_ledger(predictions, settings)
    return predictions


def main():
    parser = argparse.ArgumentParser(description="DVPE Football Big 5 pipeline")
    parser.add_argument("--fixtures", default="data/fixtures/upcoming.csv", help="Path to fixture-card CSV")
    parser.add_argument("--leagues", default="E0,SP1,I1,D1,F1", help="Comma-separated league codes")
    parser.add_argument("--seasons-back", type=int, default=2, help="Number of prior seasons to ingest")
    args = parser.parse_args()

    leagues = args.leagues.split(",")
    predictions = run(args.fixtures, leagues, args.seasons_back)

    if predictions.empty:
        print("No predictions generated.")
        return

    qualified = predictions[predictions["qualified"]]
    print(f"\n{len(predictions)} fixtures scored, {len(qualified)} qualify as +EV draw bets.\n")
    cols = ["date", "league", "home_team", "away_team", "model_p_draw", "market_p_draw", "odds_draw", "ev", "stake_pct"]
    print(predictions[cols].to_string(index=False))


if __name__ == "__main__":
    main()

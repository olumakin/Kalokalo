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
from src.ingestion.data_loader import validate_matches
from src.ingestion.historical import load_all, load_settings, season_codes
from src.ingestion.normalizer import normalize_dataframe
from src.ingestion.odds_feed import load_fixture_csv
from src.models.dixon_coles import DixonColesModel
from src.models.simulator import match_probabilities, top_scorelines
from src.tracking.supabase_ledger import get_supabase_client, prediction_row_from_pipeline, write_predictions

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
        (h1, a1, p1), (h2, a2, p2) = top_scorelines(probs["matrix"], n=2)

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
            "top_score": f"{h1}-{a1}",
            "top_score_prob": p1,
            "alt_score": f"{h2}-{a2}",
            "alt_score_prob": p2,
            "market_p_home": market_h,
            "market_p_draw": market_d,
            "market_p_away": market_a,
            "odds_home": row["odds_home"],
            "odds_draw": row["odds_draw"],
            "odds_away": row["odds_away"],
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
    matches, report = validate_matches(normalize_dataframe(raw))
    logger.info(
        "Historical validation: %d/%d rows kept (dropped %d invalid core, %d same-team, %d duplicate; "
        "%d implausible odds cleared, %d rows with a usable market price)",
        report["output_rows"], report["input_rows"], report["dropped_missing_core"],
        report["dropped_same_team"], report["dropped_duplicate"], report["odds_invalidated"], report["odds_coverage"],
    )
    return matches


def fit_model(
    matches: pd.DataFrame, settings: dict, goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
) -> DixonColesModel:
    """Fit the Dixon-Coles model on a canonical-schema match history.

    `goal_columns` defaults to actual goals; pass ("home_xg", "away_xg")
    to fit on blended xG instead (src/ingestion/sources.py) — see
    DixonColesModel.fit's docstring for the caveats of doing so.
    """
    model_cfg = settings["model"]
    model = DixonColesModel(
        min_matches=model_cfg["min_matches_for_team_rating"],
        max_iter=model_cfg["max_optimizer_iterations"],
        method=model_cfg["optimizer_method"],
        rho_init=model_cfg["rho_init"],
    ).fit(matches, xi=model_cfg["xi_decay"], goal_columns=goal_columns)
    logger.info(
        "Model fit: mu0=%.4f gamma=%.4f rho=%.4f converged=%s fallback_used=%s teams=%d",
        model.mu0_, model.gamma_, model.rho_, model.converged_, model.fallback_used_, len(model.teams_),
    )
    return model


def record_predictions_to_supabase(
    predictions: pd.DataFrame, model: DixonColesModel, settings: dict,
    price_source: str, run_id: str | None = None, client=None,
) -> dict:
    """Write every prediction row to Supabase — the only ledger (Phase 0
    revised; the local file ledger has been retired, see
    scripts/migrate_local_ledger_to_supabase.py for its one-time
    migration). Shared by both entry points into this pipeline (the CLI's
    `run()` below and app.py's admin-gated "Run pipeline" action) so
    there's exactly one write path, not two copies that could drift.

    Never raises: a Supabase outage must never block showing predictions
    on screen or crash the CLI. Returns a status dict — {"run_id",
    "status", "failed"} — for the caller to surface (System Health panel
    in the UI, a log line for the CLI).
    """
    import uuid

    if predictions.empty:
        return {"run_id": run_id, "status": "no predictions to record", "failed": 0}

    run_id = run_id or str(uuid.uuid4())
    client = client or get_supabase_client()
    if client is None:
        return {"run_id": run_id, "status": "Not configured", "failed": len(predictions)}

    rows = [
        prediction_row_from_pipeline(row, run_id, model, settings, price_source)
        for _, row in predictions.iterrows()
    ]
    failed = write_predictions(rows, client=client)
    status = "OK" if failed == 0 else f"{failed} row(s) failed this run"
    return {"run_id": run_id, "status": status, "failed": failed}


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
    result = record_predictions_to_supabase(predictions, model, settings, price_source="cli_fixture_csv")
    logger.info("Supabase ledger: %s (run_id=%s, failed=%d)", result["status"], result["run_id"], result["failed"])
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

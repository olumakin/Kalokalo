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
import math
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


def build_predictions(fixtures: pd.DataFrame, models: dict[str, DixonColesModel], settings: dict) -> pd.DataFrame:
    """`models` is one fitted DixonColesModel per league (see `fit_model`),
    not a single shared fit -- each fixture is scored by its own league's
    model. A fixture whose league has no entry (historical data couldn't
    be fetched or fit for it) is skipped with a warning, the same
    fail-soft behavior already used elsewhere in this pipeline for a
    missing league, rather than raising and losing every other league's
    predictions over one gap.
    """
    edge_cfg = settings["edge"]
    devig_method = settings["devig"]["method"]

    rows = []
    for _, row in fixtures.iterrows():
        model = models.get(row["league"])
        if model is None:
            logger.warning(
                "No fitted model for league %r; skipping %s v %s",
                row["league"], row["home_team"], row["away_team"],
            )
            continue
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
        # Per-league calibration-confidence discount (see settings.yaml
        # edge.calibration_stake_multiplier) -- applied before the
        # exposure caps below, since it's a correction to this specific
        # stake's own size, not a portfolio-level constraint.
        f_kelly *= edge_cfg.get("calibration_stake_multiplier", {}).get(row["league"], 1.0)

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


def trim_to_rolling_window(
    matches: pd.DataFrame, window_days: int, as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Restrict `matches` to a strict rolling window ending at `as_of`
    (default: now) -- the exact window logic WP3's validated harness
    applies at each retrain step (src.validation.gate_harness
    .run_league_evaluation: `date < current_date and date >= current_date
    - window_days`), so the live pipeline's training window is the one
    WP3 actually evaluated, not however many whole seasons happened to
    be downloaded. Confirmed these differ in practice: at seasons_back=2
    (the old admin default), whole-season loading pulled ~395 extra,
    older matches a strict 730-day window would already have rolled off
    (11% more data, all outside what WP3 validated).
    """
    as_of = as_of or pd.Timestamp.now(tz="UTC")
    window_start = as_of - pd.Timedelta(days=window_days)
    dates = pd.to_datetime(matches["date"], utc=True)
    return matches[(dates < as_of) & (dates >= window_start)]


def load_historical_matches(
    leagues: list[str], seasons_back: int, settings: dict, window_days: int | None = None,
) -> pd.DataFrame:
    """Download (or read from cache) and normalize Big 5 historical
    results, then -- if `window_days` is given -- trim to a strict
    rolling window of that many days ending now (trim_to_rolling_window
    above). `seasons_back` only controls how many whole-season CSVs get
    fetched; the caller is responsible for it covering at least
    `window_days` (callers deriving window_days from the validated
    config should size seasons_back from the same number, not pick it
    independently -- see app.py's admin "Historical window" control).
    """
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
    if window_days is not None:
        before = len(matches)
        matches = trim_to_rolling_window(matches, window_days)
        logger.info("Trimmed to rolling %d-day window: %d/%d rows kept", window_days, len(matches), before)
    return matches


def fit_model(
    matches: pd.DataFrame, settings: dict, goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
) -> dict[str, DixonColesModel]:
    """Fit one Dixon-Coles model per league, not one joint fit across all
    of them -- returns {league: fitted_model}.

    A single joint fit across the Big 5 pins one shared mu0 (baseline
    scoring rate) and one shared gamma (home advantage) across leagues
    that genuinely differ on both (Bundesliga runs meaningfully
    higher-scoring than Ligue 1, for instance), and turns the
    optimization into one much larger problem instead of several small
    ones -- both a source of bias and, empirically, a real convergence
    risk (confirmed live: a joint fit over the full Big 5 failed to
    converge and silently fell back to independent Poisson, per
    DixonColesModel.fit's failsafe). This is also the shape WP3's
    gate-evaluation harness validates
    (src.validation.gate_harness.run_league_evaluation fits per league);
    the live pipeline fitting jointly instead meant the CLV evidence WP3
    produced never actually applied to what this function returned.
    """
    model_cfg = settings["model"]
    models: dict[str, DixonColesModel] = {}
    for league in sorted(matches["league"].unique()):
        league_matches = matches[matches["league"] == league]
        try:
            model = DixonColesModel(
                min_matches=model_cfg["min_matches_for_team_rating"],
                max_iter=model_cfg["max_optimizer_iterations"],
                method=model_cfg["optimizer_method"],
                rho_init=model_cfg["rho_init"],
            ).fit(league_matches, xi=model_cfg["xi_decay"], goal_columns=goal_columns)
        except ValueError as exc:
            # One league's fit failing (e.g. too little data) shouldn't
            # cost every other league its predictions -- skip it and
            # warn, the same fail-soft handling already used for a
            # league with no historical data at all.
            logger.warning("Model fit failed for league %r, skipping: %s", league, exc)
            continue
        logger.info(
            "Model fit [%s]: mu0=%.4f gamma=%.4f rho=%.4f converged=%s fallback_used=%s teams=%d",
            league, model.mu0_, model.gamma_, model.rho_, model.converged_, model.fallback_used_, len(model.teams_),
        )
        models[league] = model

    if not models:
        raise ValueError("No league could be fit -- see warnings above for each league's failure reason")
    return models


def record_predictions_to_supabase(
    predictions: pd.DataFrame, models: dict[str, DixonColesModel], settings: dict,
    price_source: str, run_id: str | None = None, client=None, window_days: int | None = None,
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
        prediction_row_from_pipeline(
            row, run_id, models.get(row["league"]), settings, price_source, window_days=window_days,
        )
        for _, row in predictions.iterrows()
    ]
    failed = write_predictions(rows, client=client)
    status = "OK" if failed == 0 else f"{failed} row(s) failed this run"
    return {"run_id": run_id, "status": status, "failed": failed}


def run(
    fixtures_path: str, leagues: list[str], seasons_back: int,
    settings_path: str | None = None, window_days: int | None = None,
) -> pd.DataFrame:
    """CLI entry path: download historical data, fit, score a fixture CSV, log to ledger.

    This writes to the same production predictions table app.py's admin
    "Run pipeline" button does, so it's locked to the same validated
    window the same way: `window_days` defaults to the WP3-validated
    settings.model.rolling_window_days (730) rather than whatever
    `seasons_back` whole-season loading happens to span. Pass a
    different `window_days` only to deliberately run an experiment --
    the resulting predictions are logged with that window_days, so
    app.py's public gate correctly shows them as forecast-only rather
    than silently treating a CLI run as validated just because it came
    from this entry point instead of the admin button.

    `seasons_back` is bumped up automatically if it wouldn't cover
    `window_days` worth of raw data -- it only controls how many whole
    season CSVs get fetched, not the window itself.
    """
    settings = load_settings() if settings_path is None else yaml.safe_load(open(settings_path))
    window_days = window_days if window_days is not None else settings["model"]["rolling_window_days"]
    seasons_back = max(seasons_back, math.ceil(window_days / 365) + 1)

    matches = load_historical_matches(leagues, seasons_back, settings, window_days=window_days)
    if matches.empty:
        logger.error("No historical matches available; cannot fit model. Populate data/historical/ or check network access.")
        return pd.DataFrame()

    models = fit_model(matches, settings)

    fixtures = load_fixture_csv(fixtures_path)
    if fixtures.empty:
        logger.warning("No fixtures loaded from %s", fixtures_path)
        return pd.DataFrame()

    predictions = build_predictions(fixtures, models, settings)
    result = record_predictions_to_supabase(
        predictions, models, settings, price_source="cli_fixture_csv", window_days=window_days,
    )
    logger.info("Supabase ledger: %s (run_id=%s, failed=%d)", result["status"], result["run_id"], result["failed"])
    return predictions


def main():
    parser = argparse.ArgumentParser(description="DVPE Football Big 5 pipeline")
    parser.add_argument("--fixtures", default="data/fixtures/upcoming.csv", help="Path to fixture-card CSV")
    parser.add_argument("--leagues", default="E0,SP1,I1,D1,F1", help="Comma-separated league codes")
    parser.add_argument(
        "--seasons-back", type=int, default=2,
        help="Minimum prior seasons to ingest -- bumped up automatically if it wouldn't cover --window-days",
    )
    parser.add_argument(
        "--window-days", type=int, default=None,
        help="Rolling window length in days; defaults to the WP3-validated config value (730). "
             "A non-default value is recorded as such and shows as forecast-only in the live app.",
    )
    args = parser.parse_args()

    leagues = args.leagues.split(",")
    predictions = run(args.fixtures, leagues, args.seasons_back, window_days=args.window_days)

    if predictions.empty:
        print("No predictions generated.")
        return

    qualified = predictions[predictions["qualified"]]
    print(f"\n{len(predictions)} fixtures scored, {len(qualified)} qualify as +EV draw bets.\n")
    cols = ["date", "league", "home_team", "away_team", "model_p_draw", "market_p_draw", "odds_draw", "ev", "stake_pct"]
    print(predictions[cols].to_string(index=False))


if __name__ == "__main__":
    main()

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

from src.config import AppConfig, get_config
from src.engine import DomainPredictionEngine
from src.ingestion.data_loader import validate_matches
from src.ingestion.historical import load_all, load_settings, season_codes
from src.ingestion.normalizer import normalize_dataframe
from src.ingestion.odds_feed import load_fixture_csv
from src.models.dixon_coles import DixonColesModel
from src.tracking.ledger import Ledger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _resolve_app_config(settings: dict | None = None) -> AppConfig:
    """Resolve AppConfig, merging partial dict overrides with default config if needed."""
    if settings is None:
        return get_config()
    try:
        return AppConfig.model_validate(settings)
    except Exception:
        base = get_config().model_dump()
        for k, v in settings.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                base[k].update(v)
            else:
                base[k] = v
        return AppConfig.model_validate(base)


def build_predictions(
    fixtures: pd.DataFrame,
    model: DixonColesModel | dict[str, DixonColesModel],
    settings: dict | None = None,
) -> pd.DataFrame:
    """Score upcoming fixtures by delegating to the single authoritative DomainPredictionEngine (M01)."""
    if fixtures.empty:
        return pd.DataFrame()

    models_dict = model if isinstance(model, dict) else {lg: model for lg in fixtures["league"].unique()}
    cfg = _resolve_app_config(settings)
    engine = DomainPredictionEngine(config=cfg)
    return engine.predict_slate(fixtures, models_dict)


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


def fit_models_by_league(
    matches: pd.DataFrame,
    settings: dict | None = None,
    goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
) -> dict[str, DixonColesModel]:
    """Fit one independent Dixon-Coles model per league delegating to DomainPredictionEngine (M01)."""
    cfg = _resolve_app_config(settings)
    engine = DomainPredictionEngine(config=cfg)
    return engine.fit_leagues(matches, goal_columns=goal_columns)


def fit_model(
    matches: pd.DataFrame,
    settings: dict | None = None,
    goal_columns: tuple[str, str] = ("home_goals", "away_goals"),
) -> DixonColesModel:
    """Fit single Dixon-Coles model delegating to DomainPredictionEngine settings (M01)."""
    cfg = _resolve_app_config(settings)
    engine = DomainPredictionEngine(config=cfg)
    m_cfg = engine.config.model
    return DixonColesModel(
        min_matches=m_cfg.min_matches_for_team_rating,
        max_iter=m_cfg.max_optimizer_iterations,
        method=m_cfg.optimizer_method,
        rho_init=m_cfg.rho_init,
    ).fit(matches, xi=m_cfg.xi_decay, goal_columns=goal_columns)



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
            timestamp=pd.Timestamp.now(tz="UTC"),
        )


def run(fixtures_path: str, leagues: list[str], seasons_back: int, settings_path: str | None = None) -> pd.DataFrame:
    """CLI entry path: download historical data, fit, score a fixture CSV, log to ledger."""
    settings = load_settings() if settings_path is None else yaml.safe_load(open(settings_path))

    matches = load_historical_matches(leagues, seasons_back, settings)
    if matches.empty:
        logger.error("No historical matches available; cannot fit model. Populate data/historical/ or check network access.")
        return pd.DataFrame()

    models = fit_models_by_league(matches, settings)

    fixtures = load_fixture_csv(fixtures_path)
    if fixtures.empty:
        logger.warning("No fixtures loaded from %s", fixtures_path)
        return pd.DataFrame()

    predictions = build_predictions(fixtures, models, settings)
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

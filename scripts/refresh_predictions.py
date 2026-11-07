"""
Headless equivalent of app.py's admin "Run pipeline" button. Unlike
src.pipeline's CLI (`python -m src.pipeline --fixtures ...`), which
requires a static fixture-card CSV, this auto-discovers upcoming
fixtures the same way the button does (src.ingestion.odds_feed
.get_upcoming_fixtures: live Odds API, else the free
football-data.co.uk schedule, else the bundled sample card) -- so it
can run unattended on a schedule instead of needing an admin to log
in and click the button.

    python -m scripts.refresh_predictions [--leagues E0,SP1,I1,D1,F1] [--window-days 730]

Needs the same Supabase env vars as the rest of this codebase
(SUPABASE_URL, SUPABASE_ANON_KEY), plus ODDS_API_KEY in the
environment for a live odds feed (optional -- falls back to the free
schedule, then the sample card, same as app.py).
"""
from __future__ import annotations

import argparse
import logging
import math
import os

from src.ingestion.historical import load_settings
from src.ingestion.odds_feed import get_upcoming_fixtures
from src.pipeline import build_predictions, fit_model, load_historical_matches, record_predictions_to_supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh live Matchday predictions (admin Run-pipeline equivalent)")
    parser.add_argument("--leagues", default=None, help="Comma-separated league codes; defaults to every configured league")
    parser.add_argument("--window-days", type=int, default=None, help="Defaults to the WP3-validated config value (730)")
    args = parser.parse_args()

    settings = load_settings()
    leagues = args.leagues.split(",") if args.leagues else sorted(settings["leagues"].keys())
    window_days = args.window_days if args.window_days is not None else settings["model"]["rolling_window_days"]
    seasons_back = math.ceil(window_days / 365) + 1

    matches = load_historical_matches(leagues, seasons_back, settings, window_days=window_days)
    if matches.empty:
        logger.error("No historical matches available; cannot fit model.")
        raise SystemExit(1)

    models = fit_model(matches, settings)

    # Scoped to whichever leagues actually ended up in `matches` -- a
    # league the model never saw historical data for has every team
    # "unseen" (same reasoning as app.py's admin Run-pipeline block).
    fixture_leagues = sorted(matches["league"].unique().tolist())
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    fixtures, fixture_source_used = get_upcoming_fixtures(fixture_leagues, odds_api_key)
    if fixtures.empty:
        logger.error("No upcoming fixtures available from any source.")
        raise SystemExit(1)

    predictions = build_predictions(fixtures, models, settings)
    result = record_predictions_to_supabase(
        predictions, models, settings, price_source=fixture_source_used, window_days=window_days,
    )

    logger.info(
        "Recorded %d predictions (source=%s, run_id=%s, status=%s, failed=%d)",
        len(predictions), fixture_source_used, result["run_id"], result["status"], result["failed"],
    )
    if result["failed"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

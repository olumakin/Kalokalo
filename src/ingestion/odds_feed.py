"""
Upcoming-fixture odds ingestion.

MVP source is a curated local CSV under data/fixtures/ (one row per
upcoming match with 1X2 odds). An optional live-API path is provided for
later wiring to a real odds provider, gated behind an API key so its
absence never breaks the pipeline.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd
import requests

from src.ingestion.normalizer import CANONICAL_COLUMNS, load_team_mappings, resolve_team

logger = logging.getLogger(__name__)

FIXTURE_COLUMNS = [
    "date", "league", "home_team", "away_team",
    "odds_home", "odds_draw", "odds_away",
]


def normalize_fixture_dataframe(df: pd.DataFrame, mappings: dict | None = None) -> pd.DataFrame:
    """Normalize a raw fixture-card DataFrame with columns:
    date, league, home_team, away_team, odds_home, odds_draw, odds_away

    home_team/away_team may be raw source names (resolved via
    team_mappings.json) or already-canonical codes. Shared by both the
    on-disk CSV loader and any in-memory source (a file upload, a demo
    fixture card) so both paths stay consistent.
    """
    if df.empty:
        return pd.DataFrame(columns=FIXTURE_COLUMNS)

    df = df.copy()
    mappings = mappings or load_team_mappings()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["home_team"] = df.apply(lambda r: resolve_team(str(r["home_team"]), r["league"], mappings), axis=1)
    df["away_team"] = df.apply(lambda r: resolve_team(str(r["away_team"]), r["league"], mappings), axis=1)

    return df[FIXTURE_COLUMNS].reset_index(drop=True)


def load_fixture_csv(path: str | Path, mappings: dict | None = None) -> pd.DataFrame:
    """Load a curated fixture-card CSV from disk and normalize it."""
    path = Path(path)
    if not path.exists():
        logger.warning("Fixture file not found: %s", path)
        return pd.DataFrame(columns=FIXTURE_COLUMNS)
    return normalize_fixture_dataframe(pd.read_csv(path), mappings)


# The Odds API's own sport keys — not our internal league codes.
ODDS_API_SPORT_KEYS = {
    "E0": "soccer_epl",
    "SP1": "soccer_spain_la_liga",
    "I1": "soccer_italy_serie_a",
    "D1": "soccer_germany_bundesliga1",
    "F1": "soccer_france_ligue_one",
}


def fetch_live_odds(
    league: str, api_key: str | None = None, base_url: str | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch live upcoming-fixture 1X2 odds from The Odds API, averaged
    across every bookmaker the response includes — a genuine multi-book
    consensus price, not just whichever bookmaker happens to be first in
    the payload.

    Requires ODDS_API_KEY (or an explicit api_key). Any failure — missing
    key, unmapped league, network error, unexpected schema — logs a
    warning and returns an empty frame so callers can fall back to a
    local fixture CSV. The Odds API's free tier serves live/upcoming
    odds only; it has no historical-odds endpoint, so this is a fixture-
    side source, not a historical one (see src/ingestion/sources.py for
    historical blending).
    """
    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key:
        logger.warning("ODDS_API_KEY not set; skipping live odds fetch for %s", league)
        return pd.DataFrame(columns=FIXTURE_COLUMNS)

    sport_key = ODDS_API_SPORT_KEYS.get(league)
    if sport_key is None:
        logger.warning("No Odds API sport key mapped for league %r", league)
        return pd.DataFrame(columns=FIXTURE_COLUMNS)

    base_url = base_url or "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    try:
        sess = session or requests
        resp = sess.get(
            base_url.format(sport_key=sport_key),
            params={"apiKey": api_key, "regions": "eu", "markets": "h2h"},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live odds fetch failed for %s: %s", league, exc)
        return pd.DataFrame(columns=FIXTURE_COLUMNS)

    rows = []
    for event in payload:
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        home_prices, draw_prices, away_prices = [], [], []

        for bookmaker in event.get("bookmakers", []):
            market = next((m for m in bookmaker.get("markets", []) if m.get("key") == "h2h"), None)
            if market is None:
                continue
            price_by_name = {o["name"]: o["price"] for o in market.get("outcomes", [])}
            if home_team in price_by_name:
                home_prices.append(price_by_name[home_team])
            if "Draw" in price_by_name:
                draw_prices.append(price_by_name["Draw"])
            if away_team in price_by_name:
                away_prices.append(price_by_name[away_team])

        if not (home_prices and draw_prices and away_prices):
            continue  # no bookmaker quoted a full 1X2 market for this event

        rows.append({
            "date": event.get("commence_time"),
            "league": league,
            "home_team": home_team,
            "away_team": away_team,
            "odds_home": sum(home_prices) / len(home_prices),
            "odds_draw": sum(draw_prices) / len(draw_prices),
            "odds_away": sum(away_prices) / len(away_prices),
            "n_bookmakers": len(event.get("bookmakers", [])),
        })

    return pd.DataFrame(rows, columns=FIXTURE_COLUMNS + ["n_bookmakers"])

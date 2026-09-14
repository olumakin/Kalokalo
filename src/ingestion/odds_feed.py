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


def load_fixture_csv(path: str | Path, mappings: dict | None = None) -> pd.DataFrame:
    """Load a curated fixture-card CSV with columns:
    date, league, home_team, away_team, odds_home, odds_draw, odds_away

    home_team/away_team may be raw source names (resolved via
    team_mappings.json) or already-canonical codes.
    """
    path = Path(path)
    if not path.exists():
        logger.warning("Fixture file not found: %s", path)
        return pd.DataFrame(columns=FIXTURE_COLUMNS)

    df = pd.read_csv(path)
    mappings = mappings or load_team_mappings()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["home_team"] = df.apply(lambda r: resolve_team(str(r["home_team"]), r["league"], mappings), axis=1)
    df["away_team"] = df.apply(lambda r: resolve_team(str(r["away_team"]), r["league"], mappings), axis=1)

    return df[FIXTURE_COLUMNS].reset_index(drop=True)


def fetch_live_odds(league: str, api_key: str | None = None, base_url: str | None = None) -> pd.DataFrame:
    """Fetch upcoming fixture odds from a live provider.

    Requires ODDS_API_KEY (or an explicit api_key). Any failure — missing
    key, network error, unexpected schema — logs a warning and returns an
    empty frame so callers can fall back to the local fixture CSV.
    """
    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key:
        logger.warning("ODDS_API_KEY not set; skipping live odds fetch for %s", league)
        return pd.DataFrame(columns=FIXTURE_COLUMNS)

    base_url = base_url or "https://api.the-odds-api.com/v4/sports/{league}/odds"
    try:
        resp = requests.get(
            base_url.format(league=league),
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
        try:
            outcomes = event["bookmakers"][0]["markets"][0]["outcomes"]
            price_by_name = {o["name"]: o["price"] for o in outcomes}
            rows.append({
                "date": event.get("commence_time"),
                "league": league,
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "odds_home": price_by_name.get(event.get("home_team")),
                "odds_draw": price_by_name.get("Draw"),
                "odds_away": price_by_name.get(event.get("away_team")),
            })
        except (KeyError, IndexError):
            continue

    return pd.DataFrame(rows, columns=FIXTURE_COLUMNS)

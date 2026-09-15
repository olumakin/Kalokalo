"""
Upcoming-fixture odds ingestion.

MVP source is a curated local CSV under data/fixtures/ (one row per
upcoming match with 1X2 odds). An optional live-API path is provided for
later wiring to a real odds provider, gated behind an API key so its
absence never breaks the pipeline.
"""
from __future__ import annotations

import io
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
    if len(api_key.strip()) <= 10:
        # Real Odds API keys are 32-char hex strings; anything this short
        # is obviously not one — skip the round trip rather than let the
        # server reject it (same graceful-empty-result outcome, cheaper).
        logger.warning("ODDS_API_KEY looks malformed (too short); skipping live odds fetch for %s", league)
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


FREE_FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"


def fetch_free_schedule(
    leagues: list[str], url: str | None = None, session: requests.Session | None = None,
    mappings: dict | None = None,
) -> pd.DataFrame:
    """Free, no-auth upcoming-fixture sheet from football-data.co.uk —
    the same provider as the historical results CSVs, refreshed roughly
    weekly (Fridays) with the coming weekend's Big 5 matches and Bet365
    pre-match 1X2 odds. No API key, but no live-market movement either
    (The Odds API is the live-consensus path; this is a static schedule).

    A row missing any of the three 1X2 prices is dropped, not filled
    with a placeholder odds value — a fabricated price would make the
    EV computed against it meaningless, not just approximate, on a tool
    whose entire purpose is finding real mispricings.

    Returns an empty frame (logged warning) on any network, schema, or
    parsing failure, matching the rest of src/ingestion.
    """
    url = url or FREE_FIXTURES_URL
    empty = pd.DataFrame(columns=FIXTURE_COLUMNS)
    try:
        sess = session or requests
        resp = sess.get(url, timeout=20)
        resp.raise_for_status()
        # football-data.co.uk CSVs are Latin-1 (team names with accents);
        # decode from raw bytes rather than trust response.text's guess.
        raw = pd.read_csv(io.BytesIO(resp.content), encoding="latin1")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch free fixture schedule: %s", exc)
        return empty

    required = {"Div", "Date", "HomeTeam", "AwayTeam", "B365H", "B365D", "B365A"}
    if not required.issubset(raw.columns):
        logger.warning("Unexpected schema from free fixture schedule (missing columns)")
        return empty

    raw = raw[raw["Div"].isin(leagues)]
    if raw.empty:
        return empty

    complete = raw.dropna(subset=["B365H", "B365D", "B365A"])
    dropped = len(raw) - len(complete)
    if dropped:
        logger.info("Dropped %d fixture(s) missing a full 1X2 price from the free schedule", dropped)

    df = pd.DataFrame({
        "date": pd.to_datetime(complete["Date"], dayfirst=True, errors="coerce"),
        "league": complete["Div"],
        "home_team": complete["HomeTeam"],
        "away_team": complete["AwayTeam"],
        "odds_home": complete["B365H"].astype(float),
        "odds_draw": complete["B365D"].astype(float),
        "odds_away": complete["B365A"].astype(float),
    }).dropna(subset=["date"])

    return normalize_fixture_dataframe(df, mappings)


# Explicit labels for which tier of the fallback chain actually supplied
# a given get_upcoming_fixtures() result — the caller decides how (or
# whether) to surface this, per this module's usual UI-framework-agnostic
# design; it's simpler and more honest than inferring it from incidental
# column differences between sources.
FIXTURE_SOURCE_LIVE_ODDS = "live_odds"
FIXTURE_SOURCE_FREE_SCHEDULE = "free_schedule"
FIXTURE_SOURCE_SAMPLE_CARD = "sample_card"


def get_upcoming_fixtures(
    leagues: list[str],
    api_key: str | None = None,
    fallback_path: str | Path = "data/fixtures/upcoming.csv",
) -> tuple[pd.DataFrame, str]:
    """Automatically fetch upcoming fixtures across `leagues` with no
    caller-side mode selection, trying three tiers in order and using
    the first that returns anything:

      1. Live consensus odds from The Odds API, if `api_key` is set.
      2. The free football-data.co.uk weekly fixture sheet (no key).
      3. The bundled sample fixture card at `fallback_path` — a final,
         always-available safety net (e.g. this sandbox has no outbound
         network access at all, so every run here hits this tier).

    Returns (fixtures, source_label) where source_label is one of the
    FIXTURE_SOURCE_* constants above. Never raises purely because a
    remote source is unavailable — same pattern as the rest of
    src/ingestion — and stays UI-framework-agnostic (only `logging`, no
    `streamlit`) so it's unit-testable without a Streamlit runtime; the
    caller (app.py) decides how to surface the source to the user.
    """
    if api_key:
        frames = [fetch_live_odds(league, api_key=api_key) for league in leagues]
        frames = [f for f in frames if not f.empty]
        if frames:
            combined = pd.concat(frames, ignore_index=True)
            # fetch_live_odds returns The Odds API's own raw team-name
            # strings (e.g. "Manchester City"), not our canonical codes.
            return normalize_fixture_dataframe(combined), FIXTURE_SOURCE_LIVE_ODDS

    free = fetch_free_schedule(leagues)
    if not free.empty:
        return free, FIXTURE_SOURCE_FREE_SCHEDULE

    logger.info("No live or free fixture data available for %s; falling back to %s", leagues, fallback_path)
    # Unlike fetch_live_odds/fetch_free_schedule above, load_fixture_csv
    # has no `leagues` concept of its own (the CLI's direct call site in
    # src/pipeline.py wants the whole curated card, unfiltered) — filter
    # here instead of widening that function's contract.
    sample = load_fixture_csv(fallback_path)
    if not sample.empty:
        sample = sample[sample["league"].isin(leagues)].reset_index(drop=True)
    return sample, FIXTURE_SOURCE_SAMPLE_CARD

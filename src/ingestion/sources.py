"""
# WP8: unwired from the live app pending re-evaluation. This module
# still exists and is still tested, but Phase 0 (revised) removed
# source blending / "Fit on xG" as an admin option in app.py — see the
# README's "Remove xG from the live path" note.

Multi-source historical data blending.

football-data.co.uk supplies match results and closing 1X2 odds but no
shot-quality signal — a 1-0 scoreline can easily have been a 2-2 on
underlying chances. Understat's match-level xG is a lower-variance
proxy for attacking/defensive quality; blending it in can stabilize the
Dixon-Coles fit (pass the resulting home_xg/away_xg columns to
DixonColesModel.fit(goal_columns=("home_xg", "away_xg"))), particularly
for teams with few matches.

Each source returns its own schema; this module resolves team names to
canonical codes and merges everything onto the canonical (date, league,
home_team, away_team) key. football-data.co.uk is always the base — it
is the only source here with actual match results — other sources are
supplemental columns joined onto it.

Only two sources are implemented: football-data.co.uk (results/odds)
and Understat (xG). The Odds API is a separate, fixture-side source
(see src/ingestion/odds_feed.py) rather than a historical blend input —
its free tier serves live upcoming odds, not historical odds. Betfair
Exchange is a real extension point but isn't implemented: its API-NG
requires certificate-based login and a registered application key,
which this project has no credentials for; FBref is likewise a natural
follow-on (another xG source) but was left out to avoid doubling
scraper-maintenance surface for limited incremental benefit over
Understat alone.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from src.ingestion.data_loader import validate_matches
from src.ingestion.historical import load_all, season_codes
from src.ingestion.normalizer import CANONICAL_COLUMNS, load_team_mappings, normalize_dataframe, resolve_team
from src.ingestion.understat_xg import fetch_league_season_xg

logger = logging.getLogger(__name__)

SOURCE_FOOTBALL_DATA = "football-data.co.uk (Match Logs & Closing Odds)"
SOURCE_UNDERSTAT = "Understat (Rolling xG & Shot Quality)"

ALL_SOURCES = [SOURCE_FOOTBALL_DATA, SOURCE_UNDERSTAT]
DEFAULT_SOURCES = [SOURCE_FOOTBALL_DATA]

BLEND_STRICT = "Strict Intersection (All sources must match)"
BLEND_CONSENSUS = "Blended Consensus (keep every base match)"


def _load_understat_xg(leagues: list[str], seasons_back: int, mappings: dict) -> pd.DataFrame:
    """Fetch + resolve Understat xG for every league/season in range."""
    end_year = date.today().year
    frames = []
    for league in leagues:
        for season_start in range(end_year - seasons_back, end_year + 1):
            df = fetch_league_season_xg(league, season_start)
            if df.empty:
                continue
            df = df.copy()
            df["league"] = league
            frames.append(df)

    empty = pd.DataFrame(columns=["date", "league", "home_team", "away_team", "home_xg", "away_xg"])
    if not frames:
        return empty

    xg = pd.concat(frames, ignore_index=True)
    xg["home_team"] = xg.apply(lambda r: resolve_team(r["home_team_raw"], r["league"], mappings), axis=1)
    xg["away_team"] = xg.apply(lambda r: resolve_team(r["away_team_raw"], r["league"], mappings), axis=1)
    xg = xg[["date", "league", "home_team", "away_team", "home_xg", "away_xg"]]
    # Defensive: a merge key seen more than once would fan out into
    # duplicate rows on the join below (e.g. overlapping season windows,
    # or a source reporting the same match twice).
    return xg.drop_duplicates(subset=["date", "league", "home_team", "away_team"])


def load_and_blend_sources(
    selected_sources: list[str],
    leagues: list[str],
    seasons_back: int,
    settings: dict,
    blend_mode: str = BLEND_CONSENSUS,
) -> pd.DataFrame:
    """Load and merge the selected historical data sources.

    blend_mode:
      BLEND_STRICT    -> inner join: only base matches that also have a
                          row from every other selected source survive
                          (guarantees full coverage on every selected
                          column, at the cost of dropping matches the
                          supplemental source doesn't have).
      BLEND_CONSENSUS -> left join off the football-data.co.uk base:
                          every base match is kept; supplemental columns
                          (e.g. home_xg/away_xg) are NaN where a source
                          has no matching row.

    Returns canonical-schema matches (see normalizer.CANONICAL_COLUMNS)
    plus any additional columns contributed by other selected sources.
    Never raises purely because a supplemental source is unavailable —
    each source degrades to "no extra columns" on failure, same as the
    rest of src/ingestion.
    """
    if SOURCE_FOOTBALL_DATA not in selected_sources:
        logger.warning(
            "football-data.co.uk deselected, but it is the only source of match "
            "results/goals in this pipeline — re-including it as the base."
        )

    mappings = load_team_mappings()
    seasons = season_codes(date.today().year - seasons_back, date.today().year)
    raw = load_all(leagues, seasons, cache_dir=settings["data_source"]["cache_dir"])
    base = normalize_dataframe(raw, mappings)
    if base.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    base, validation_report = validate_matches(base)
    if base.empty:
        logger.warning("All %d loaded matches failed validation: %s", validation_report["input_rows"], validation_report)
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    how = "inner" if blend_mode == BLEND_STRICT else "left"

    if SOURCE_UNDERSTAT in selected_sources:
        xg = _load_understat_xg(leagues, seasons_back, mappings)
        if xg.empty:
            if how == "inner":
                logger.warning(
                    "Understat selected under strict blending but returned no data; result will be empty."
                )
                base = base.iloc[0:0]
        else:
            base = base.merge(xg, on=["date", "league", "home_team", "away_team"], how=how)

    return base

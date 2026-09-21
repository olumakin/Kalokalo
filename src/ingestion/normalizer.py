"""
Canonical team-identity resolution and schema unification.

Every downstream module (Dixon-Coles fitting, backtesting, ledger) keys
teams by a canonical code (e.g. "TOT") rather than a raw source string
(e.g. "Spurs" vs "Tottenham") so the optimizer never fragments one club's
rating across multiple aliases.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from src.ingestion.data_loader import select_entry_close_odds, select_match_odds

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEAM_MAPPINGS_PATH = REPO_ROOT / "config" / "team_mappings.json"

CANONICAL_COLUMNS = [
    "date", "league", "season", "home_team", "away_team",
    "home_goals", "away_goals", "result",
    "odds_home", "odds_draw", "odds_away", "price_source",
]

EVALUATION_COLUMNS = CANONICAL_COLUMNS + [
    "entry_home", "entry_draw", "entry_away", "entry_source",
    "close_home", "close_draw", "close_away", "close_source",
    "retail_draw",
]


def load_team_mappings(path: Path = TEAM_MAPPINGS_PATH) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def resolve_team(name: str, league: str, mappings: dict) -> str:
    """Map a raw source team name to its canonical code.

    Falls back to an uppercased slug (and logs a warning) for names not
    present in config/team_mappings.json, so the optimizer still treats
    an unmapped team as a single stable entity rather than crashing.
    """
    league_map = mappings.get(league, {})
    if name in league_map:
        return league_map[name]
    fallback = "".join(ch for ch in name.upper() if ch.isalnum())[:3] or "UNK"
    logger.warning("No canonical mapping for %r in league %s; using fallback %r", name, league, fallback)
    return fallback


def build_display_names(league: str, mappings: dict | None = None) -> dict[str, str]:
    """Build a code -> human-readable club name lookup for one league.

    Codes are only unique *within* a league (e.g. "MON" is both Monaco in
    Ligue 1 and Monza in Serie A), so this is intentionally scoped to a
    single league rather than flattened globally. Picks the longest raw
    alias mapped to each code as a cheap proxy for "the fuller name"
    (e.g. "Tottenham" over "Spurs", "Atletico Madrid" over "Ath Madrid")
    without needing a second hand-maintained display-name file.
    """
    mappings = mappings or load_team_mappings()
    league_map = mappings.get(league, {})
    display_names: dict[str, str] = {}
    for raw_name, code in league_map.items():
        if code not in display_names or len(raw_name) > len(display_names[code]):
            display_names[code] = raw_name
    return display_names


def resolve_display_name(code: str, league: str, mappings: dict | None = None) -> str:
    """Best-effort code -> display name for one league; falls back to the
    code itself if it isn't in team_mappings.json (e.g. an uploaded CSV
    with codes for an unmapped club)."""
    mappings = mappings or load_team_mappings()
    return build_display_names(league, mappings).get(code, code)


def normalize_dataframe(raw: pd.DataFrame, mappings: dict | None = None) -> pd.DataFrame:
    """Convert raw football-data.co.uk rows into the canonical schema.

    Market odds are picked per row via a season-aware hierarchy (see
    src.ingestion.data_loader.select_match_odds / ODDS_HIERARCHY): the
    "best" available column family a given season/row actually
    populates wins, rather than one hardcoded pair — a `price_source`
    column records which tier was used, or NA if none was available.
    """
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    mappings = mappings or load_team_mappings()
    df = raw.copy()

    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    if df.empty:
        # dropna above can consume every row (e.g. an all-unparseable
        # Date column); df.apply(..., result_type="expand") on a 0-row
        # frame returns 0 columns, which breaks the fixed-width odds
        # unpacking below — short-circuit instead.
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df["home_team"] = df.apply(lambda r: resolve_team(r["HomeTeam"], r["league"], mappings), axis=1)
    df["away_team"] = df.apply(lambda r: resolve_team(r["AwayTeam"], r["league"], mappings), axis=1)

    df["home_goals"] = df["FTHG"].astype(int)
    df["away_goals"] = df["FTAG"].astype(int)
    df["result"] = df["FTR"] if "FTR" in df.columns else df.apply(
        lambda r: "H" if r["home_goals"] > r["away_goals"] else ("A" if r["home_goals"] < r["away_goals"] else "D"),
        axis=1,
    )

    odds = df.apply(select_match_odds, axis=1, result_type="expand")
    odds.columns = ["odds_home", "odds_draw", "odds_away", "price_source"]
    df[["odds_home", "odds_draw", "odds_away", "price_source"]] = odds

    out = df[CANONICAL_COLUMNS].sort_values("date").reset_index(drop=True)
    return out


def normalize_evaluation_dataframe(raw: pd.DataFrame, mappings: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Convert raw football-data.co.uk rows into the evaluation schema (A10).

    Preserves both canonical odds and explicit opening, closing, and retail
    price legs (entry_*, close_*, retail_draw) via select_entry_close_odds.
    Counts missing-price reasons for auditing and returns (df, report).
    Guarantees date is datetime64[ns, UTC].
    """
    empty_report = {
        "input_rows": len(raw),
        "valid_rows": 0,
        "missing_entry_count": 0,
        "missing_close_count": 0,
        "missing_retail_count": 0,
        "fully_priced_count": 0,
    }
    if raw.empty:
        return pd.DataFrame(columns=EVALUATION_COLUMNS), empty_report

    mappings = mappings or load_team_mappings()
    df = raw.copy()

    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    if df.empty:
        return pd.DataFrame(columns=EVALUATION_COLUMNS), empty_report

    # Ensure UTC timezone
    if df["date"].dt.tz is None:
        df["date"] = df["date"].dt.tz_localize("UTC")
    else:
        df["date"] = df["date"].dt.tz_convert("UTC")

    df["home_team"] = df.apply(lambda r: resolve_team(r["HomeTeam"], r["league"], mappings), axis=1)
    df["away_team"] = df.apply(lambda r: resolve_team(r["AwayTeam"], r["league"], mappings), axis=1)

    df["home_goals"] = df["FTHG"].astype(int)
    df["away_goals"] = df["FTAG"].astype(int)
    df["result"] = df["FTR"] if "FTR" in df.columns else df.apply(
        lambda r: "H" if r["home_goals"] > r["away_goals"] else ("A" if r["home_goals"] < r["away_goals"] else "D"),
        axis=1,
    )

    # 1. Canonical match odds (best available)
    canonical_odds = df.apply(select_match_odds, axis=1, result_type="expand")
    canonical_odds.columns = ["odds_home", "odds_draw", "odds_away", "price_source"]
    df[["odds_home", "odds_draw", "odds_away", "price_source"]] = canonical_odds

    # 2. Distinct entry and close legs for evaluation (A10)
    eval_odds = df.apply(select_entry_close_odds, axis=1, result_type="expand")
    for col in eval_odds.columns:
        df[col] = eval_odds[col]

    # Calculate missing-price reasons
    missing_entry = df["entry_draw"].isna()
    missing_close = df["close_draw"].isna()
    missing_retail = df["retail_draw"].isna()
    fully_priced = (~missing_entry) & (~missing_close) & (~missing_retail)

    report = {
        "input_rows": len(raw),
        "valid_rows": len(df),
        "missing_entry_count": int(missing_entry.sum()),
        "missing_close_count": int(missing_close.sum()),
        "missing_retail_count": int(missing_retail.sum()),
        "fully_priced_count": int(fully_priced.sum()),
    }

    out = df[EVALUATION_COLUMNS].sort_values("date").reset_index(drop=True)
    return out, report

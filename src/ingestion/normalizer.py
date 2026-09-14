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

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEAM_MAPPINGS_PATH = REPO_ROOT / "config" / "team_mappings.json"

CANONICAL_COLUMNS = [
    "date", "league", "season", "home_team", "away_team",
    "home_goals", "away_goals", "result",
    "odds_home", "odds_draw", "odds_away",
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

    Uses average closing odds (AvgH/D/A) when available, falling back to
    Bet365 odds (B365H/D/A) otherwise.
    """
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    mappings = mappings or load_team_mappings()
    df = raw.copy()

    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])

    df["home_team"] = df.apply(lambda r: resolve_team(r["HomeTeam"], r["league"], mappings), axis=1)
    df["away_team"] = df.apply(lambda r: resolve_team(r["AwayTeam"], r["league"], mappings), axis=1)

    df["home_goals"] = df["FTHG"].astype(int)
    df["away_goals"] = df["FTAG"].astype(int)
    df["result"] = df["FTR"] if "FTR" in df.columns else df.apply(
        lambda r: "H" if r["home_goals"] > r["away_goals"] else ("A" if r["home_goals"] < r["away_goals"] else "D"),
        axis=1,
    )

    for target, primary, secondary in (
        ("odds_home", "AvgH", "B365H"),
        ("odds_draw", "AvgD", "B365D"),
        ("odds_away", "AvgA", "B365A"),
    ):
        if primary in df.columns:
            df[target] = df[primary].fillna(df[secondary]) if secondary in df.columns else df[primary]
        elif secondary in df.columns:
            df[target] = df[secondary]
        else:
            df[target] = pd.NA

    out = df[CANONICAL_COLUMNS].sort_values("date").reset_index(drop=True)
    return out

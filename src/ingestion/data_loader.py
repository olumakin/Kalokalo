"""
WP1: season-aware market-odds selection and strict validation for
historical Big 5 matches.

football-data.co.uk's odds columns are not consistent across seasons —
the "true" market-consensus column has been renamed and re-scoped over
the site's history (Betbrain's BbAvH/D/A up to the 2018/19 season,
renamed AvgH/D/A from 2019/20 onward), some bookmaker columns (Pinnacle)
were only added partway through the archive, and older seasons may
carry only Bet365. Picking a single hardcoded column pair silently
starves the model of odds for whichever seasons don't have it.
select_match_odds resolves this per row against whichever columns are
actually populated, in a fixed priority order, and records which tier
was used (price_source) for auditability.

validate_matches is a separate, explicit step: it drops rows that would
corrupt the Dixon-Coles fit (missing/invalid date, team, or goal data)
or are exact duplicates, and neutralizes implausible single-row odds
(<=1.01, not a valid decimal price) without dropping the match itself —
a row lacking a usable price is still valid goal-model training data;
src/validation/backtest.py already skips NaN-odds rows when scoring
market comparisons.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Ordered by trustworthiness as a fair draw price, most preferred first:
# a multi-book average is the least single-book-biased signal, Pinnacle
# is widely regarded the sharpest single book, Bet365 has the most
# complete historical column coverage as a baseline, and a market
# maximum is kept only as a last resort (it systematically overstates
# the fair price by construction). BbAv*/BbMx* are the pre-2019/20
# column names for the same Avg*/Max* concepts.
ODDS_HIERARCHY: list[tuple[str, str]] = [
    ("Avg", "market average"),
    ("BbAv", "market average (legacy Betbrain naming, pre-2019/20)"),
    ("PSC", "Pinnacle closing"),
    ("PS", "Pinnacle"),
    ("B365", "Bet365"),
    ("Max", "market maximum"),
    ("BbMx", "market maximum (legacy Betbrain naming, pre-2019/20)"),
]


def select_match_odds(row: pd.Series) -> tuple[float | None, float | None, float | None, str | None]:
    """Pick the first fully-populated H/D/A odds triple for one row, in
    ODDS_HIERARCHY priority order. Returns (None, None, None, None) if no
    tier has all three prices for this row."""
    for prefix, label in ODDS_HIERARCHY:
        h_col, d_col, a_col = f"{prefix}H", f"{prefix}D", f"{prefix}A"
        if h_col not in row.index or d_col not in row.index or a_col not in row.index:
            continue
        h, d, a = row[h_col], row[d_col], row[a_col]
        if pd.notna(h) and pd.notna(d) and pd.notna(a):
            return float(h), float(d), float(a), label
    return None, None, None, None


def select_entry_close_odds(row: pd.Series) -> dict:
    """Two genuinely distinct price snapshots for CLV analysis (WP3),
    as opposed to select_match_odds' single "best available" price.

    football-data.co.uk's historical CSVs don't record a full odds time
    series, so there's no literal "price when the bet was placed" for a
    backtest. The standard proxy in the sports-betting research
    literature when true bet-timestamps aren't available is Pinnacle's
    own opening line (PS) vs. its own closing line (PSC) — comparing a
    single sharp book against itself avoids the cross-book bias a
    Pinnacle-vs-Bet365 comparison would introduce. Falls back to Bet365
    for either leg where Pinnacle columns aren't populated for that
    row/season (older seasons, before Pinnacle was tracked).

    retail_draw is Bet365's own draw price specifically — for a
    separate "retail pass" evaluating CLV against a price a
    recreational bettor could actually have gotten, alongside the
    sharp-book comparison.
    """
    entry_h = entry_d = entry_a = entry_source = None
    for prefix, label in (("PS", "Pinnacle (opening)"), ("B365", "Bet365")):
        h, d, a = row.get(f"{prefix}H"), row.get(f"{prefix}D"), row.get(f"{prefix}A")
        if pd.notna(h) and pd.notna(d) and pd.notna(a):
            entry_h, entry_d, entry_a, entry_source = float(h), float(d), float(a), label
            break

    close_h = close_d = close_a = close_source = None
    for prefix, label in (("PSC", "Pinnacle closing"), ("Avg", "market average"), ("BbAv", "market average (legacy)")):
        h, d, a = row.get(f"{prefix}H"), row.get(f"{prefix}D"), row.get(f"{prefix}A")
        if pd.notna(h) and pd.notna(d) and pd.notna(a):
            close_h, close_d, close_a, close_source = float(h), float(d), float(a), label
            break

    retail_d = row.get("B365D")
    retail_d = float(retail_d) if pd.notna(retail_d) else None

    return {
        "entry_home": entry_h, "entry_draw": entry_d, "entry_away": entry_a, "entry_source": entry_source,
        "close_home": close_h, "close_draw": close_d, "close_away": close_a, "close_source": close_source,
        "retail_draw": retail_d,
    }


def validate_matches(df: pd.DataFrame, min_valid_odds: float = 1.01) -> tuple[pd.DataFrame, dict]:
    """Strict validation of canonical-schema historical matches (see
    src.ingestion.normalizer.CANONICAL_COLUMNS).

    Returns (clean_df, report); report is a dict of row counts by
    outcome, safe to log or surface in the UI.
    """
    report = {
        "input_rows": len(df),
        "dropped_missing_core": 0,
        "dropped_same_team": 0,
        "dropped_duplicate": 0,
        "odds_invalidated": 0,
        "odds_coverage": 0,
        "output_rows": 0,
    }
    if df.empty:
        return df, report

    out = df.copy()

    core_cols = ["date", "home_team", "away_team", "home_goals", "away_goals"]
    missing_core = out[core_cols].isna().any(axis=1)

    # Check for blank or whitespace team names (A13)
    blank_teams = (
        out["home_team"].astype(str).str.strip().isin(["", "None", "nan", "UNK"])
        | out["away_team"].astype(str).str.strip().isin(["", "None", "nan", "UNK"])
    )

    # Check for non-finite or negative goals (A13)
    non_finite_goals = (
        ~np.isfinite(pd.to_numeric(out["home_goals"], errors="coerce"))
        | ~np.isfinite(pd.to_numeric(out["away_goals"], errors="coerce"))
    )
    negative_goals = (out["home_goals"].fillna(0) < 0) | (out["away_goals"].fillna(0) < 0)

    drop_core = missing_core | blank_teams | non_finite_goals | negative_goals
    report["dropped_missing_core"] = int(drop_core.sum())
    out = out[~drop_core]

    same_team = out["home_team"] == out["away_team"]
    report["dropped_same_team"] = int(same_team.sum())
    out = out[~same_team]

    dedup_keys = [c for c in ("date", "league", "home_team", "away_team") if c in out.columns]
    is_dup = out.duplicated(subset=dedup_keys, keep="first")
    report["dropped_duplicate"] = int(is_dup.sum())
    out = out[~is_dup]

    odds_cols = ["odds_home", "odds_draw", "odds_away"]
    if set(odds_cols).issubset(out.columns):
        has_odds = out[odds_cols].notna().all(axis=1)
        numeric_odds_valid = (
            np.isfinite(pd.to_numeric(out["odds_home"], errors="coerce"))
            & np.isfinite(pd.to_numeric(out["odds_draw"], errors="coerce"))
            & np.isfinite(pd.to_numeric(out["odds_away"], errors="coerce"))
        )
        implausible = has_odds & (
            ~numeric_odds_valid
            | (out["odds_home"] <= min_valid_odds)
            | (out["odds_draw"] <= min_valid_odds)
            | (out["odds_away"] <= min_valid_odds)
        )
        report["odds_invalidated"] = int(implausible.sum())
        out.loc[implausible, odds_cols] = pd.NA
        if "price_source" in out.columns:
            out.loc[implausible, "price_source"] = pd.NA
        report["odds_coverage"] = int(out[odds_cols].notna().all(axis=1).sum())

    report["output_rows"] = len(out)
    return out.reset_index(drop=True), report

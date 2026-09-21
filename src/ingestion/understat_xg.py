"""
# WP8: unwired from the live app pending re-evaluation. This module and
# its consumer (src/ingestion/sources.py) still exist and are still
# tested, but Phase 0 (revised) removed Understat as a selectable
# source from app.py's public deployment — see the README's "Remove xG
# from the live path" note.

Understat.com historical match Expected Goals (xG) ingestion.

Understat has no public API. Match-level xG is embedded as an
obfuscated JSON blob inside a <script> tag on each league's season page
— a long-documented pattern (used by most open-source Understat
scrapers) where the payload is `\\xHH`-escaped to make naive scraping
harder, then JSON-decoded.

Understat's markup can change without notice, and this sandbox has no
outbound network access to verify a live pull. Every extraction is
wrapped so a parsing failure degrades to an empty result with a logged
warning rather than raising — consistent with the rest of src/ingestion
— so a pipeline run can always proceed without this source.
"""
from __future__ import annotations

import json
import logging
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Understat's own league slugs, keyed by our canonical league codes.
UNDERSTAT_LEAGUE_SLUGS = {
    "E0": "EPL",
    "SP1": "La_liga",
    "I1": "Serie_A",
    "D1": "Bundesliga",
    "F1": "Ligue_1",
}
BASE_URL = "https://understat.com/league/{slug}/{season}"

XG_COLUMNS = ["date", "home_team_raw", "away_team_raw", "home_xg", "away_xg"]

# Matches `var datesData = JSON.parse('<escaped payload>')` (also matches
# other *Data JSON.parse(...) assignments on the same page — the caller
# filters to the one preceded by the target variable name).
_JSON_PARSE_RE = re.compile(r"JSON\.parse\('(?P<payload>.+?)'\)", re.DOTALL)


def _decode_understat_payload(raw: str) -> list[dict]:
    """Decode Understat's \\xHH-escaped JSON payload into Python objects."""
    decoded = raw.encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
    return json.loads(decoded)


def extract_json_variable(html: str, variable_name: str) -> list[dict]:
    """Pull one `var <variable_name> = JSON.parse('...')` payload out of
    an Understat page's inline <script> content."""
    for match in _JSON_PARSE_RE.finditer(html):
        window_start = max(0, match.start() - 60)
        preceding = html[window_start:match.start()]
        if variable_name in preceding:
            return _decode_understat_payload(match.group("payload"))
    raise ValueError(f"Could not locate {variable_name!r} in Understat page")


def fetch_league_season_xg(
    league: str, season_start_year: int, session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch per-match historical xG for one league/season from Understat.

    Returns columns: date, home_team_raw, away_team_raw, home_xg, away_xg.
    Team names are Understat's own raw strings (not yet resolved to our
    canonical codes) — resolve them the same way as football-data.co.uk's
    raw HomeTeam/AwayTeam, via `src.ingestion.normalizer.resolve_team`.

    Returns an empty frame (with a logged warning) on any network or
    parsing failure, rather than raising.
    """
    slug = UNDERSTAT_LEAGUE_SLUGS.get(league)
    if slug is None:
        logger.warning("No Understat league slug mapped for %r", league)
        return pd.DataFrame(columns=XG_COLUMNS)

    url = BASE_URL.format(slug=slug, season=season_start_year)
    try:
        sess = session or requests
        resp = sess.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        matches = extract_json_variable(resp.text, "datesData")
    except Exception as exc:  # noqa: BLE001 - network/markup drift, all "unavailable"
        logger.warning("Could not fetch/parse Understat %s %s: %s", league, season_start_year, exc)
        return pd.DataFrame(columns=XG_COLUMNS)

    rows = []
    for m in matches:
        if not m.get("isResult"):
            continue
        try:
            rows.append({
                "date": pd.to_datetime(m["datetime"]).normalize(),
                "home_team_raw": m["h"]["title"],
                "away_team_raw": m["a"]["title"],
                "home_xg": float(m["xG"]["h"]),
                "away_xg": float(m["xG"]["a"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return pd.DataFrame(rows, columns=XG_COLUMNS)

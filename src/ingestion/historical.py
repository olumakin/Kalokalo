"""
Historical result + odds ingestion for the Big 5 European leagues.

Source: football-data.co.uk season CSVs (E0, SP1, I1, D1, F1).
Downloads are cached to disk so the pipeline never re-fetches a season
it already has, and so tests / offline runs can operate on cached data.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests
import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"

# Columns we care about from the raw football-data.co.uk schema.
RAW_COLUMNS = [
    "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
    "B365H", "B365D", "B365A",
    "AvgH", "AvgD", "AvgA",
]


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def season_codes(start_year: int, end_year: int) -> list[str]:
    """Build football-data.co.uk season codes, e.g. 2023 -> '2324'."""
    return [f"{str(y)[-2:]}{str(y + 1)[-2:]}" for y in range(start_year, end_year + 1)]


def download_league_season(
    league: str,
    season: str,
    cache_dir: Path,
    base_url: str,
    session: requests.Session | None = None,
    force: bool = False,
) -> pd.DataFrame | None:
    """Fetch one league/season CSV, using the on-disk cache when present.

    Returns None (with a logged warning) if the data cannot be obtained,
    rather than raising, so a pipeline run can proceed with partial data.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{league}_{season}.csv"

    if cache_path.exists() and not force:
        return pd.read_csv(cache_path)

    url = base_url.format(season=season, league=league)
    try:
        sess = session or requests
        resp = sess.get(url, timeout=20)
        resp.raise_for_status()
        cache_path.write_bytes(resp.content)
        return pd.read_csv(cache_path)
    except Exception as exc:  # noqa: BLE001 - network/parse errors are all "unavailable"
        logger.warning("Could not download %s %s: %s", league, season, exc)
        return None


def load_all(
    leagues: list[str],
    seasons: list[str],
    cache_dir: str | Path = "data/historical",
    base_url: str | None = None,
) -> pd.DataFrame:
    """Load and concatenate raw historical rows for the given leagues/seasons.

    Any league/season combination that cannot be fetched is skipped with a
    warning; the function never raises purely due to network unavailability.
    """
    settings = load_settings()
    base_url = base_url or settings["data_source"]["historical_base_url"]
    cache_dir = Path(cache_dir)

    frames = []
    for league in leagues:
        for season in seasons:
            df = download_league_season(league, season, cache_dir, base_url)
            if df is None or df.empty:
                continue
            keep = [c for c in RAW_COLUMNS if c in df.columns]
            df = df[keep].copy()
            df["league"] = league
            df["season"] = season
            frames.append(df)

    if not frames:
        logger.warning("No historical data could be loaded for %s / %s", leagues, seasons)
        return pd.DataFrame(columns=RAW_COLUMNS + ["league", "season"])

    return pd.concat(frames, ignore_index=True)

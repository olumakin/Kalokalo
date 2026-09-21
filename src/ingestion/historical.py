"""
Historical result + odds ingestion for the Big 5 European leagues.

Source: football-data.co.uk season CSVs (E0, SP1, I1, D1, F1).
Downloads are cached to disk so the pipeline never re-fetches a season
it already has, and so tests / offline runs can operate on cached data.
"""
from __future__ import annotations

from datetime import date
import logging
from pathlib import Path

import pandas as pd
import requests
import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"

# Columns we care about from the raw football-data.co.uk schema. The
# odds columns cover every tier in src.ingestion.data_loader.ODDS_HIERARCHY
# — different seasons only populate a subset of these (see that module's
# docstring), so all of them are kept here rather than just one pair.
RAW_COLUMNS = [
    "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
    "AvgH", "AvgD", "AvgA",
    "BbAvH", "BbAvD", "BbAvA",
    "PSCH", "PSCD", "PSCA",
    "PSH", "PSD", "PSA",
    "B365H", "B365D", "B365A",
    "MaxH", "MaxD", "MaxA",
    "BbMxH", "BbMxD", "BbMxA",
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
    max_age_hours: int = 24,
) -> pd.DataFrame | None:
    """Fetch one league/season CSV, validating content before caching (IMP03).

    For active seasons, automatically refreshes if the cache file is older
    than `max_age_hours` (A08).
    """
    import io
    import time

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{league}_{season}.csv"

    # Check freshness (A08): if file exists, check if it's the current season and expired
    if cache_path.exists() and not force:
        file_age_hours = (time.time() - cache_path.stat().st_mtime) / 3600.0
        # Determine if this is an active current/recent season
        current_year = date.today().year
        current_season_code = f"{str(current_year)[-2:]}{str(current_year + 1)[-2:]}"
        prev_season_code = f"{str(current_year - 1)[-2:]}{str(current_year)[-2:]}"
        is_active = season in (current_season_code, prev_season_code)

        if not is_active or file_age_hours < max_age_hours:
            try:
                return pd.read_csv(cache_path)
            except Exception as exc:
                logger.warning("Cached file %s is corrupt (%s); re-downloading", cache_path, exc)

    url = base_url.format(season=season, league=league)
    try:
        sess = session or requests
        resp = sess.get(url, timeout=20)
        resp.raise_for_status()

        # Validate in-memory before writing to disk (IMP03)
        content = resp.content
        if not content or len(content) < 50:
            logger.warning("Empty or truncated response from %s for %s %s", url, league, season)
            return None

        # Verify it can be parsed as CSV with basic match columns
        df = pd.read_csv(io.BytesIO(content))
        required = {"Date", "HomeTeam", "AwayTeam"}
        if not required.issubset(df.columns):
            logger.warning("Downloaded content from %s lacks required columns %s; not caching", url, required)
            return None

        # Content is valid, commit to cache atomically
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_bytes(content)
        temp_path.replace(cache_path)
        return df
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not download %s %s: %s", league, season, exc)
        if cache_path.exists():
            try:
                logger.info("Falling back to existing cache for %s %s", league, season)
                return pd.read_csv(cache_path)
            except Exception:
                pass
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


def assemble_historical_evaluation_dataset(
    leagues: list[str],
    seasons: list[str],
    cache_dir: str | Path = "data/historical",
    base_url: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Assemble canonical historical evaluation dataset across leagues and seasons (A10).

    Downloads/loads raw CSVs, extracts distinct entry and close pricing legs via
    normalize_evaluation_dataframe, runs strict match validation, and aggregates
    missing-price stats and data provenance for the gate harness.
    """
    from src.ingestion.data_loader import validate_matches
    from src.ingestion.normalizer import EVALUATION_COLUMNS, normalize_evaluation_dataframe

    raw_df = load_all(leagues=leagues, seasons=seasons, cache_dir=cache_dir, base_url=base_url)
    if raw_df.empty:
        return pd.DataFrame(columns=EVALUATION_COLUMNS), {
            "total_raw_rows": 0,
            "valid_matches": 0,
            "missing_entry_count": 0,
            "missing_close_count": 0,
            "missing_retail_count": 0,
            "fully_priced_count": 0,
        }

    norm_df, norm_report = normalize_evaluation_dataframe(raw_df)
    clean_df, val_report = validate_matches(norm_df)

    if not clean_df.empty:
        if clean_df["date"].dt.tz is None:
            clean_df["date"] = clean_df["date"].dt.tz_localize("UTC")
        else:
            clean_df["date"] = clean_df["date"].dt.tz_convert("UTC")

    manifest = {
        "leagues": leagues,
        "seasons": seasons,
        "total_raw_rows": len(raw_df),
        "normalized_rows": len(norm_df),
        "clean_rows": len(clean_df),
        "validation_report": val_report,
        "missing_price_report": norm_report,
    }
    return clean_df, manifest

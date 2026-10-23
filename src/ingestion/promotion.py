"""
Promotion detection for the Big 5 leagues.

football-data.co.uk's raw CSVs carry no explicit "promoted this season"
flag (src.ingestion.historical/data_loader). This module infers it: a
team present in a league's data for a season but absent from that
league's calendar-immediately-preceding season is a reasonable proxy
for "newly promoted" (or otherwise newly returned to the division --
the same signal a Dixon-Coles fit needs regardless of exactly how the
team got there).

The proxy needs a real preceding-season roster to compare against. A
season whose immediately-preceding season isn't itself present in the
corpus -- the corpus's first season for that league, or a gap in the
archive -- has no such baseline, so no team is labeled promoted for it;
treating every team as "new" in that case would be wrong (most of them
simply aren't new, we just can't see their prior season).
"""
from __future__ import annotations

import pandas as pd

# football-data.co.uk season codes only span modern seasons (roughly
# 1993 onward); a 2-digit year below this pivot is read as 19xx, at or
# above it as 20xx. Matches the convention season_codes() in
# src.ingestion.historical already produces codes under (it only ever
# emits 20xx-range codes in practice, but parsing accepts either).
_CENTURY_PIVOT = 80


def _season_start_year(season: str) -> int:
    """'2324' -> 2023. Inverse of src.ingestion.historical.season_codes."""
    yy = int(str(season)[:2])
    return (1900 if yy >= _CENTURY_PIVOT else 2000) + yy


def _season_code_for_start_year(start_year: int) -> str:
    return f"{str(start_year)[-2:]}{str(start_year + 1)[-2:]}"


def preceding_season(season: str) -> str:
    """The calendar season immediately before `season`, e.g. '2324' -> '2223'."""
    return _season_code_for_start_year(_season_start_year(season) - 1)


def promoted_teams_by_season(matches: pd.DataFrame) -> dict[tuple[str, str], set[str]]:
    """For every (league, season) present in `matches`, the set of
    canonical team codes inferred to be newly promoted that season.

    `matches` must carry canonical `league`, `season`, `home_team`,
    `away_team` columns (src.ingestion.normalizer.CANONICAL_COLUMNS).
    A (league, season) whose preceding season has no roster in
    `matches` at all gets an empty set -- not "everyone", since we have
    no baseline to call anyone new against.
    """
    if matches.empty:
        return {}

    rosters: dict[tuple[str, str], set[str]] = {}
    for (league, season), group in matches.groupby(["league", "season"]):
        rosters[(league, str(season))] = set(group["home_team"]) | set(group["away_team"])

    result: dict[tuple[str, str], set[str]] = {}
    for (league, season), roster in rosters.items():
        preceding = rosters.get((league, preceding_season(season)))
        result[(league, season)] = set() if preceding is None else roster - preceding
    return result

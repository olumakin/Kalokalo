"""
Per-league "promoted-team prior" for DixonColesModel's regularization
target (src.models.dixon_coles).

The problem this replaces: a team with fewer than
`min_matches_for_team_rating` matches inside the model's fit window
(almost always a newly-promoted side in the first months of a new
season, since the 730-day rolling window production is locked to --
src.pipeline.trim_to_rolling_window -- rolls off essentially all of its
prior top-flight history) gets pinned/shrunk toward (0, 0), the league
median. A newly-promoted team is usually below the league median, not
at it, so that target systematically overrates exactly the teams it's
meant to regularize.

This module estimates what a typical newly-promoted team's rating
actually looks like, per league, from real history: for every
(league, season) where `src.ingestion.promotion.promoted_teams_by_season`
identifies at least one promoted team, fit a *single-season*
DixonColesModel on that season's own matches alone (xi=0 -- a season's
results all carry equal weight for this purpose; no recency decay) and
read off the promoted teams' fitted (alpha, beta). A full top-flight
season gives every team on the order of ~38 fixtures -- comfortably
above `min_matches_for_team_rating` -- so this rating is a real fitted
signal, not an artifact of the same shrinkage the prior is meant to
replace. Averaging these (league, season, team) records within each
league gives the per-league prior.
"""
from __future__ import annotations

import logging

import pandas as pd

from src.ingestion.promotion import promoted_teams_by_season
from src.models.dixon_coles import DixonColesModel

logger = logging.getLogger(__name__)

DEFAULT_PRIOR = (0.0, 0.0)


def compute_promoted_team_priors(
    matches: pd.DataFrame,
    min_matches_for_team_rating: int = 15,
    max_iter: int = 500,
    method: str = "L-BFGS-B",
    default_prior: tuple[float, float] = DEFAULT_PRIOR,
) -> dict[str, tuple[float, float]]:
    """Return {league: (alpha, beta)} -- the average fitted attack/
    defense rating of a newly-promoted team in its first season back
    in that league, across every season in `matches` where the
    promotion proxy found at least one.

    `matches` should be the full historical corpus (every season
    available, not trimmed to the production rolling window) -- this
    is exactly the "fuller data" source a sparse in-window team's
    rating can't itself provide. A league with no identifiable
    promoted-team-season (too few seasons in the corpus for the
    promotion proxy to have a baseline, or every single-season fit for
    it failed to converge) falls back to `default_prior` -- the old
    league-median target -- rather than raising.
    """
    if matches.empty:
        return {}

    promoted_by_season = promoted_teams_by_season(matches)
    records: dict[str, list[tuple[float, float]]] = {}

    for (league, season), promoted in promoted_by_season.items():
        if not promoted:
            continue
        season_matches = matches[(matches["league"] == league) & (matches["season"] == season)]
        try:
            model = DixonColesModel(
                min_matches=min_matches_for_team_rating, max_iter=max_iter, method=method,
            ).fit(season_matches, xi=0.0)
        except ValueError as exc:
            logger.warning(
                "Could not fit %s season %s to estimate its promoted-team prior: %s", league, season, exc,
            )
            continue

        for team in promoted:
            if team not in model.teams_:
                # Promoted per the roster diff but somehow absent from
                # the fit (e.g. mid-season withdrawal) -- nothing to
                # read a rating off of for this team-season.
                continue
            records.setdefault(league, []).append((model.alpha_[team], model.beta_[team]))

    priors: dict[str, tuple[float, float]] = {}
    for league, pairs in records.items():
        alpha_mean = sum(a for a, _ in pairs) / len(pairs)
        beta_mean = sum(b for _, b in pairs) / len(pairs)
        priors[league] = (alpha_mean, beta_mean)
        logger.info(
            "Promoted-team prior [%s]: alpha=%.4f beta=%.4f (from %d team-season observations)",
            league, alpha_mean, beta_mean, len(pairs),
        )

    for league in matches["league"].unique():
        if league not in priors:
            logger.warning(
                "No promoted-team-season observations for league %r; falling back to default_prior %s",
                league, default_prior,
            )
            priors[league] = default_prior

    return priors

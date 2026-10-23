import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "tests")
from test_dixon_coles import generate_synthetic_matches  # noqa: E402

from src.models.promoted_prior import DEFAULT_PRIOR, compute_promoted_team_priors  # noqa: E402


def _season_round_robin(league, season, start, teams, rng, home_lambda=1.4, away_lambda=1.1, rounds=4):
    """Full round-robin so every team gets >= min_matches within this
    one season alone -- the same reason a real top-flight season (~38
    fixtures/team) doesn't need this module's estimate to itself be
    shrunk."""
    rows = []
    day = 0
    for _ in range(rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                hg, ag = int(rng.poisson(home_lambda)), int(rng.poisson(away_lambda))
                rows.append({
                    "date": start + pd.Timedelta(days=day), "league": league, "season": season,
                    "home_team": home, "away_team": away, "home_goals": hg, "away_goals": ag,
                    "result": "H" if hg > ag else ("A" if hg < ag else "D"),
                })
                day += 1
    return pd.DataFrame(rows)


class TestComputePromotedTeamPriors:
    def test_promoted_team_s_weak_rating_pulls_the_prior_below_the_median(self):
        # Season 1: incumbents only. Season 2: incumbents + one
        # deliberately weak newcomer (WEAK) that loses almost every
        # match -- the exact shape a real newly-promoted side takes.
        rng = np.random.default_rng(11)
        incumbents = [f"T{i:02d}" for i in range(8)]
        season1 = _season_round_robin("E0", "2223", pd.Timestamp("2022-08-01"), incumbents, rng)

        rng2 = np.random.default_rng(12)
        season2_teams = incumbents + ["WEAK"]
        rows = []
        day = 0
        start = pd.Timestamp("2023-08-01")
        for _ in range(4):
            for home in season2_teams:
                for away in season2_teams:
                    if home == away:
                        continue
                    if "WEAK" in (home, away):
                        # WEAK concedes a lot and scores little regardless of venue.
                        hg = int(rng2.poisson(2.2)) if away == "WEAK" else int(rng2.poisson(0.3))
                        ag = int(rng2.poisson(0.3)) if away == "WEAK" else int(rng2.poisson(2.2))
                    else:
                        hg, ag = int(rng2.poisson(1.4)), int(rng2.poisson(1.1))
                    rows.append({
                        "date": start + pd.Timedelta(days=day), "league": "E0", "season": "2324",
                        "home_team": home, "away_team": away, "home_goals": hg, "away_goals": ag,
                        "result": "H" if hg > ag else ("A" if hg < ag else "D"),
                    })
                    day += 1
        season2 = pd.DataFrame(rows)

        matches = pd.concat([season1, season2], ignore_index=True)
        priors = compute_promoted_team_priors(matches, min_matches_for_team_rating=15)

        alpha, beta = priors["E0"]
        # WEAK is the only promoted team found (2223 is the corpus's
        # first season, so it contributes no promoted-team records) --
        # the league's prior should equal WEAK's own fitted rating,
        # and that rating should look like a genuinely weak team:
        # below-average attack, below-average defense.
        assert alpha < 0.0
        assert beta > 0.0

    def test_first_season_in_corpus_contributes_no_promoted_team_observations(self):
        matches = generate_synthetic_matches(n_teams=10, rounds=6)
        priors = compute_promoted_team_priors(matches, min_matches_for_team_rating=15)
        # Every team in this single-season fixture is "promoted" only
        # relative to a non-existent preceding season -- no baseline,
        # so no observations, so the fallback default applies.
        assert priors["E0"] == DEFAULT_PRIOR

    def test_empty_matches_returns_empty_dict(self):
        assert compute_promoted_team_priors(pd.DataFrame()) == {}

    def test_each_league_gets_its_own_prior(self):
        rng = np.random.default_rng(21)
        e0_incumbents = [f"E{i:02d}" for i in range(8)]
        e0_s1 = _season_round_robin("E0", "2223", pd.Timestamp("2022-08-01"), e0_incumbents, rng)
        e0_s2 = _season_round_robin("E0", "2324", pd.Timestamp("2023-08-01"), e0_incumbents + ["ENEW"], rng)

        rng2 = np.random.default_rng(22)
        d1_incumbents = [f"D{i:02d}" for i in range(8)]
        d1_s1 = _season_round_robin("D1", "2223", pd.Timestamp("2022-08-01"), d1_incumbents, rng2)
        d1_s2 = _season_round_robin("D1", "2324", pd.Timestamp("2023-08-01"), d1_incumbents + ["DNEW"], rng2)

        matches = pd.concat([e0_s1, e0_s2, d1_s1, d1_s2], ignore_index=True)
        priors = compute_promoted_team_priors(matches, min_matches_for_team_rating=15)

        assert set(priors.keys()) == {"E0", "D1"}
        # Not asserting a specific relationship between the two priors
        # (both newcomers are drawn from the same ordinary-strength
        # distribution here) -- just that each league produced its own
        # independent, finite estimate rather than sharing one.
        assert all(np.isfinite(v) for pair in priors.values() for v in pair)

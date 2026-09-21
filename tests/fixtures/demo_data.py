"""
Offline demo/synthetic data generator.

This sandbox has no outbound access to football-data.co.uk (or any live
odds provider), so the Streamlit UI needs a way to be fully explorable
without a network round-trip. This module simulates a plausible match
history and a fixture card using real canonical team codes.

Each team is given a fixed latent attack/defense strength; goals are
drawn Poisson around those strengths, and 1X2 odds are derived from the
*true* generating probabilities plus a bookmaker margin and a little
market noise. A model fit on the resulting history therefore recovers
something close to (but not exactly) the true strengths, so it
sometimes — but not always — disagrees with the noisy market: exactly
the "occasionally mispriced draw" scenario DVPE is built to find.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.ingestion.normalizer import CANONICAL_COLUMNS, load_team_mappings
from src.ingestion.odds_feed import FIXTURE_COLUMNS
from src.models.simulator import match_probabilities

DEFAULT_LEAGUE = "E0"


def _pick_teams(league: str, n_teams: int, mappings: dict | None = None) -> list[str]:
    mappings = mappings or load_team_mappings()
    codes = sorted(set(mappings.get(league, {}).values()))
    if not codes:
        codes = [f"T{i:02d}" for i in range(20)]
    return codes[:n_teams]


def _team_strengths(league: str, n_teams: int, seed: int) -> tuple[list[str], dict, dict]:
    rng = np.random.default_rng(seed)
    teams = _pick_teams(league, n_teams)
    attack = dict(zip(teams, rng.normal(0, 0.30, len(teams))))
    defense = dict(zip(teams, rng.normal(0, 0.25, len(teams))))
    return teams, attack, defense


def _lambda_mu(home: str, away: str, attack: dict, defense: dict, home_advantage: float) -> tuple[float, float]:
    lam = float(np.exp(0.35 + attack[home] + defense[away] + home_advantage))
    mu = float(np.exp(0.35 + attack[away] + defense[home]))
    return lam, mu


def _noisy_market_odds(
    lam: float, mu: float, rng: np.random.Generator, bookmaker_margin: float, noise_sigma: float = 0.04
) -> np.ndarray:
    true = match_probabilities(lam, mu, rho=-0.05)
    p_true = np.array([true["p_home"], true["p_draw"], true["p_away"]])
    p_noisy = p_true * np.exp(rng.normal(0, noise_sigma, 3))
    p_noisy = p_noisy / p_noisy.sum()
    p_book = np.clip(p_noisy * (1 + bookmaker_margin), 1e-6, None)
    return 1.0 / p_book


def generate_demo_matches(
    league: str = DEFAULT_LEAGUE,
    n_teams: int = 10,
    rounds: int = 4,
    seed: int = 42,
    horizon_days: int = 700,
    home_advantage: float = 0.28,
    bookmaker_margin: float = 0.06,
) -> pd.DataFrame:
    """Simulate a round-robin match history in canonical schema."""
    teams, attack, defense = _team_strengths(league, n_teams, seed)
    rng = np.random.default_rng(seed + 1)

    fixtures = []
    for _ in range(rounds):
        for home in teams:
            for away in teams:
                if home != away:
                    fixtures.append((home, away))

    end = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=horizon_days)
    dates = pd.date_range(start, end, periods=len(fixtures))

    rows = []
    for (home, away), match_date in zip(fixtures, dates):
        lam, mu = _lambda_mu(home, away, attack, defense, home_advantage)
        odds = _noisy_market_odds(lam, mu, rng, bookmaker_margin)
        hg = int(rng.poisson(lam))
        ag = int(rng.poisson(mu))
        rows.append({
            "date": match_date,
            "league": league,
            "season": "DEMO",
            "home_team": home,
            "away_team": away,
            "home_goals": hg,
            "away_goals": ag,
            "result": "H" if hg > ag else ("A" if hg < ag else "D"),
            "odds_home": round(float(odds[0]), 2),
            "odds_draw": round(float(odds[1]), 2),
            "odds_away": round(float(odds[2]), 2),
        })

    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


def generate_demo_fixtures(
    league: str = DEFAULT_LEAGUE,
    n_teams: int = 10,
    seed: int = 42,
    n_fixtures: int = 12,
    fixture_seed: int = 3,
    home_advantage: float = 0.28,
    bookmaker_margin: float = 0.06,
) -> pd.DataFrame:
    """Sample an upcoming fixture card using the *same* latent team
    strengths as `generate_demo_matches` (same league/n_teams/seed), so
    predictions on it are a meaningful test of the fitted model."""
    teams, attack, defense = _team_strengths(league, n_teams, seed)
    rng = np.random.default_rng(fixture_seed)

    pairs = [(h, a) for h in teams for a in teams if h != a]
    rng.shuffle(pairs)

    next_date = pd.Timestamp.today().normalize() + pd.Timedelta(days=3)
    rows = []
    for i, (home, away) in enumerate(pairs[:n_fixtures]):
        lam, mu = _lambda_mu(home, away, attack, defense, home_advantage)
        # Wider noise than the historical generator: a live fixture card is
        # a single noisy market snapshot, not an average over many closing
        # lines, so it should disagree with the fitted model more often.
        odds = _noisy_market_odds(lam, mu, rng, bookmaker_margin, noise_sigma=0.14)
        rows.append({
            "date": next_date + pd.Timedelta(days=i // 3),
            "league": league,
            "home_team": home,
            "away_team": away,
            "odds_home": round(float(odds[0]), 2),
            "odds_draw": round(float(odds[1]), 2),
            "odds_away": round(float(odds[2]), 2),
        })

    return pd.DataFrame(rows, columns=FIXTURE_COLUMNS)

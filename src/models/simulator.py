"""
10x10 scoreline probability matrix and derived market probabilities.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson

from src.models.dixon_coles import tau

DEFAULT_GRID_SIZE = 10


def check_tau_admissibility(lam: float, mu: float, rho: float) -> tuple[bool, str]:
    """Verify that all four Dixon-Coles low-score adjustment factors are non-negative."""
    t00 = 1.0 - lam * mu * rho
    t01 = 1.0 + lam * rho
    t10 = 1.0 + mu * rho
    t11 = 1.0 - rho
    if t00 < 0:
        return False, f"tau(0,0) = 1 - lam*mu*rho is negative ({t00:.4f})"
    if t01 < 0:
        return False, f"tau(0,1) = 1 + lam*rho is negative ({t01:.4f})"
    if t10 < 0:
        return False, f"tau(1,0) = 1 + mu*rho is negative ({t10:.4f})"
    if t11 < 0:
        return False, f"tau(1,1) = 1 - rho is negative ({t11:.4f})"
    return True, ""


def build_score_matrix(
    lam: float,
    mu: float,
    rho: float,
    grid_size: int = DEFAULT_GRID_SIZE,
    max_grid_size: int = 30,
    tail_tolerance: float = 1e-3,
) -> np.ndarray:
    """Build the normalized joint scoreline probability matrix P(x, y).

    Rows index home goals x, columns index away goals y, x, y in
    [0, grid_size - 1]. The Dixon-Coles tau adjustment is applied to the
    four low-score cells, verifying parameter admissibility and checking
    retained mass against `tail_tolerance` (A05).
    """
    if not (np.isfinite(lam) and np.isfinite(mu) and np.isfinite(rho)):
        raise ValueError(f"Non-finite parameters: lam={lam}, mu={mu}, rho={rho}")
    if lam <= 0 or mu <= 0:
        raise ValueError(f"Goal intensities must be positive: lam={lam}, mu={mu}")

    admissible, reason = check_tau_admissibility(lam, mu, rho)
    if not admissible:
        raise ValueError(f"Inadmissible Dixon-Coles parameters for lam={lam}, mu={mu}, rho={rho}: {reason}")

    curr_size = grid_size
    matrix = None
    while curr_size <= max_grid_size:
        x = np.arange(curr_size)
        y = np.arange(curr_size)
        xx, yy = np.meshgrid(x, y, indexing="ij")

        p_home = poisson.pmf(xx, lam)
        p_away = poisson.pmf(yy, mu)
        base = p_home * p_away

        lam_grid = np.full_like(base, lam, dtype=float)
        mu_grid = np.full_like(base, mu, dtype=float)
        tau_grid = tau(xx.astype(float), yy.astype(float), lam_grid, mu_grid, rho)

        candidate = base * tau_grid
        if (candidate < 0).any():
            raise ValueError(f"Negative probability cell generated for lam={lam}, mu={mu}, rho={rho}")

        raw_total = float(candidate.sum())
        if raw_total >= (1.0 - tail_tolerance) or curr_size >= max_grid_size:
            matrix = candidate
            break
        curr_size += 5

    total = float(matrix.sum())
    if total < (1.0 - 0.05):  # hard tolerance limit: must retain at least 95% of mass
        raise ValueError(f"Insufficient retained mass {total:.4f} below tolerance limit for lam={lam}, mu={mu}")

    return matrix / total


def prob_draw(matrix: np.ndarray) -> float:
    """P(Draw) = sum over the diagonal x == y."""
    return float(np.trace(matrix))


def prob_home_win(matrix: np.ndarray) -> float:
    return float(np.sum(np.tril(matrix, k=-1)))


def prob_away_win(matrix: np.ndarray) -> float:
    return float(np.sum(np.triu(matrix, k=1)))


def match_probabilities(lam: float, mu: float, rho: float, grid_size: int = DEFAULT_GRID_SIZE) -> dict:
    matrix = build_score_matrix(lam, mu, rho, grid_size)
    return {
        "p_home": prob_home_win(matrix),
        "p_draw": prob_draw(matrix),
        "p_away": prob_away_win(matrix),
        "matrix": matrix,
    }


def top_scorelines(matrix: np.ndarray, n: int = 2) -> list[tuple[int, int, float]]:
    """Return the `n` most probable (home_goals, away_goals, probability)
    scorelines from a fitted scoreline matrix (build_score_matrix's
    output) — real model output, not a simulated/random score."""
    flat_idx = np.argsort(matrix.ravel())[::-1][:n]
    results = []
    for idx in flat_idx:
        h, a = np.unravel_index(idx, matrix.shape)
        results.append((int(h), int(a), float(matrix[h, a])))
    return results

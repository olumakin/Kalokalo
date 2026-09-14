"""
10x10 scoreline probability matrix and derived market probabilities.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson

from src.models.dixon_coles import tau

DEFAULT_GRID_SIZE = 10


def build_score_matrix(lam: float, mu: float, rho: float, grid_size: int = DEFAULT_GRID_SIZE) -> np.ndarray:
    """Build the normalized joint scoreline probability matrix P(x, y).

    Rows index home goals x, columns index away goals y, x, y in
    [0, grid_size - 1]. The Dixon-Coles tau adjustment is applied to the
    four low-score cells, then the whole grid is renormalized so the
    total probability mass sums to 1.0 (per PID section 3.4).
    """
    x = np.arange(grid_size)
    y = np.arange(grid_size)
    xx, yy = np.meshgrid(x, y, indexing="ij")

    p_home = poisson.pmf(xx, lam)
    p_away = poisson.pmf(yy, mu)
    base = p_home * p_away

    lam_grid = np.full_like(base, lam, dtype=float)
    mu_grid = np.full_like(base, mu, dtype=float)
    tau_grid = tau(xx.astype(float), yy.astype(float), lam_grid, mu_grid, rho)

    matrix = base * tau_grid
    matrix = np.clip(matrix, 0.0, None)

    total = matrix.sum()
    if total <= 0:
        raise ValueError("Score matrix has zero total probability mass; check lambda/mu/rho")
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

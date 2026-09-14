"""
Margin removal (de-vigging) of 1X2 market odds.

Two methods are provided:
  - multiplicative: simple proportional normalization (PID 4.1).
  - shin: Shin's (1992) method, which models the overround as arising
    from informed ("insider") money rather than uniform vig, and tends
    to produce sharper true-probability estimates for favorite/longshot
    markets like the draw.
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.optimize import brentq

logger = logging.getLogger(__name__)


def implied_probabilities(odds_h: float, odds_d: float, odds_a: float) -> np.ndarray:
    return np.array([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a])


def overround(odds_h: float, odds_d: float, odds_a: float) -> float:
    return float(implied_probabilities(odds_h, odds_d, odds_a).sum())


def multiplicative_devig(odds_h: float, odds_d: float, odds_a: float) -> tuple[float, float, float]:
    """P_market,k = P_raw,k / S  (PID 4.1)."""
    p_raw = implied_probabilities(odds_h, odds_d, odds_a)
    s = p_raw.sum()
    p = p_raw / s
    return float(p[0]), float(p[1]), float(p[2])


def shin_devig(
    odds_h: float, odds_d: float, odds_a: float, max_iter: int = 100
) -> tuple[float, float, float]:
    """Shin's method: solve for the insider-trading fraction z such that
    the resulting probabilities sum to 1, then return them.

    Falls back to the multiplicative method (with a logged warning) if no
    root can be bracketed, e.g. degenerate/zero-margin odds.
    """
    p_raw = implied_probabilities(odds_h, odds_d, odds_a)
    s = p_raw.sum()

    if s <= 1.0:
        p = p_raw / s
        return float(p[0]), float(p[1]), float(p[2])

    def probs_for_z(z: float) -> np.ndarray:
        return (np.sqrt(z ** 2 + 4 * (1 - z) * (p_raw ** 2 / s)) - z) / (2 * (1 - z))

    def f(z: float) -> float:
        return float(probs_for_z(z).sum() - 1.0)

    try:
        z = brentq(f, 1e-9, 0.4, maxiter=max_iter)
        p = probs_for_z(z)
        p = p / p.sum()
        return float(p[0]), float(p[1]), float(p[2])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Shin de-vig failed to converge (%s); falling back to multiplicative", exc)
        return multiplicative_devig(odds_h, odds_d, odds_a)


def devig(odds_h: float, odds_d: float, odds_a: float, method: str = "multiplicative") -> tuple[float, float, float]:
    if method == "shin":
        return shin_devig(odds_h, odds_d, odds_a)
    if method == "multiplicative":
        return multiplicative_devig(odds_h, odds_d, odds_a)
    raise ValueError(f"Unknown de-vig method: {method!r}")

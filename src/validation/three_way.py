"""
Three-way (Home/Draw/Away) forecast scoring.

The existing log_loss/brier_score in src.validation.metrics score the
draw probability alone (a binary Draw-vs-not-Draw question), matching
the PID's draw-value focus. These score the full 3-outcome forecast —
a model that nails the draw call but is badly wrong about home/away
would look fine on the binary metrics and shouldn't.
"""
from __future__ import annotations

import numpy as np

# Outcome index convention used throughout this module: 0=Home, 1=Draw, 2=Away.
HOME, DRAW, AWAY = 0, 1, 2


def three_way_log_loss(y_true_idx: np.ndarray, p_home: np.ndarray, p_draw: np.ndarray, p_away: np.ndarray,
                        eps: float = 1e-12) -> float:
    """Multi-class log-loss: -mean(log(p[outcome actually occurred]))."""
    y = np.asarray(y_true_idx, dtype=int)
    probs = np.clip(np.column_stack([p_home, p_draw, p_away]), eps, 1.0)
    picked = probs[np.arange(len(y)), y]
    return float(-np.mean(np.log(picked)))


def rps(y_true_idx: np.ndarray, p_home: np.ndarray, p_draw: np.ndarray, p_away: np.ndarray) -> float:
    """Ranked Probability Score for the ordered 3-outcome market
    (Home < Draw < Away, a natural "goal-difference direction" ordering
    — RPS specifically rewards a forecast for being *closer* on the
    ordinal scale when it's wrong, unlike log-loss/Brier which treat
    every wrong class the same).

    RPS_i = (1 / (K-1)) * sum_{k=1}^{K} (CumP_k - CumE_k)^2, K=3.
    Lower is better; 0 is a perfect forecast.
    """
    y = np.asarray(y_true_idx, dtype=int)
    probs = np.column_stack([p_home, p_draw, p_away])
    n = len(y)

    actual = np.zeros((n, 3))
    actual[np.arange(n), y] = 1.0

    cum_p = np.cumsum(probs, axis=1)
    cum_e = np.cumsum(actual, axis=1)
    return float(np.mean(np.sum((cum_p - cum_e) ** 2, axis=1)) / 2.0)

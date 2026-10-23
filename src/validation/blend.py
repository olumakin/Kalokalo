"""
Step 7 (WP3 follow-up / PID Milestone 3 ongoing sanity check): does
blending the model's forecast with the de-vigged market add value over
either alone?

Scored on full 3-way (Home/Draw/Away) log-loss
(src.validation.three_way.three_way_log_loss) rather than the
draw-only binary metric used for calibration -- a blend that trades
draw accuracy for home/away accuracy (or vice versa) should show up
here, not be hidden by scoring only the draw call.

This is descriptive, not a gate: it answers "would an ensemble help,"
it does not itself decide anything about production staking.
"""
from __future__ import annotations

import numpy as np

from src.validation.three_way import three_way_log_loss

DEFAULT_GRID = np.linspace(0.0, 1.0, 21)


def blend_three_way_log_loss(
    y_true_idx: np.ndarray,
    model_probs: tuple[np.ndarray, np.ndarray, np.ndarray],
    market_probs: tuple[np.ndarray, np.ndarray, np.ndarray],
    weight: float,
) -> float:
    """3-way log-loss of `weight`*model + (1-weight)*market, renormalized
    per row. Both inputs already sum to 1 per row, so renormalizing only
    guards against floating-point drift, not a real redistribution."""
    model_stack = np.column_stack(model_probs)
    market_stack = np.column_stack(market_probs)
    blended = weight * model_stack + (1 - weight) * market_stack
    blended = blended / blended.sum(axis=1, keepdims=True)
    return three_way_log_loss(y_true_idx, blended[:, 0], blended[:, 1], blended[:, 2])


def best_blend_weight(
    y_true_idx: np.ndarray,
    model_probs: tuple[np.ndarray, np.ndarray, np.ndarray],
    market_probs: tuple[np.ndarray, np.ndarray, np.ndarray],
    grid: np.ndarray = DEFAULT_GRID,
) -> tuple[float, float]:
    """Grid search over blend weight w in [0,1] for the log-loss-
    minimizing blend of model and market. Returns (best_weight, its
    log_loss)."""
    scored = [(float(w), blend_three_way_log_loss(y_true_idx, model_probs, market_probs, w)) for w in grid]
    return min(scored, key=lambda t: t[1])


def compare_model_market_blend(
    y_true_idx: np.ndarray,
    p_home: np.ndarray, p_draw: np.ndarray, p_away: np.ndarray,
    market_p_home: np.ndarray, market_p_draw: np.ndarray, market_p_away: np.ndarray,
    grid: np.ndarray = DEFAULT_GRID,
) -> dict:
    """The step-7 offline blend test on one set of scored fixtures.

    If the best blend weight lands at 1.0, the market adds nothing the
    model doesn't already have; at 0.0, the reverse; an interior
    optimum with a lower log-loss than both endpoints is the sign an
    ensemble would help in production.
    """
    y = np.asarray(y_true_idx)
    model_probs = (np.asarray(p_home, dtype=float), np.asarray(p_draw, dtype=float), np.asarray(p_away, dtype=float))
    market_probs = (
        np.asarray(market_p_home, dtype=float), np.asarray(market_p_draw, dtype=float), np.asarray(market_p_away, dtype=float),
    )
    model_ll = three_way_log_loss(y, *model_probs)
    market_ll = three_way_log_loss(y, *market_probs)
    best_w, blend_ll = best_blend_weight(y, model_probs, market_probs, grid=grid)
    return {
        "n": int(len(y)),
        "model_log_loss": model_ll,
        "market_log_loss": market_ll,
        "best_blend_weight": best_w,
        "best_blend_log_loss": blend_ll,
        "blend_beats_both": bool(blend_ll < min(model_ll, market_ll) - 1e-9),
    }

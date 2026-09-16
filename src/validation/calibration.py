"""
Calibration diagnostics beyond the binned reliability curve already in
src.validation.metrics.calibration_curve.

Reliability-by-decile (10 bins) is just that function called with the
default n_bins=10 — no need to duplicate it, see gate_harness.py.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize


def _logit(p: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    p = np.clip(p, eps, 1 - eps)
    return np.log(p / (1 - p))


def calibration_slope_intercept(y_true: np.ndarray, p_pred: np.ndarray) -> tuple[float, float]:
    """Cox calibration regression: fit y ~ Bernoulli(sigmoid(a + b*logit(p_pred))).

    A well-calibrated model has intercept a ~= 0 and slope b ~= 1.
    b < 1 means the model is overconfident (predicted probabilities too
    extreme relative to the observed rate); b > 1 means underconfident.
    a != 0 indicates a systematic bias in the average predicted level.

    Returns (nan, nan) for fewer than 2 observations or if every y_true
    value is identical (the slope is undefined with no outcome variance
    to regress against).
    """
    y = np.asarray(y_true, dtype=float)
    x = _logit(np.asarray(p_pred, dtype=float))
    n = len(y)
    if n < 2 or len(np.unique(y)) < 2:
        return float("nan"), float("nan")

    def neg_log_lik(params: np.ndarray) -> float:
        a, b = params
        z = a + b * x
        # log-sum-exp form of the Bernoulli log-likelihood, numerically
        # stable for large |z| (a poorly-calibrated slope can produce
        # extreme z during the search before it converges).
        log_lik = y * (-np.logaddexp(0, -z)) + (1 - y) * (-np.logaddexp(0, z))
        return float(-np.sum(log_lik))

    result = minimize(neg_log_lik, x0=np.array([0.0, 1.0]), method="Nelder-Mead")
    a, b = result.x
    return float(a), float(b)

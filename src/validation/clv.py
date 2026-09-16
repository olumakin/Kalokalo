"""
Closing Line Value evaluation for the WP3 gate harness.

Two distinct questions, both needed for the gate decision:
  1. Baseline CLV — across *every* fixture with both an entry and a
     closing price, regardless of whether the model flagged it. This is
     the market's own natural CLV distribution (should center near 0
     for a random/no-edge selection).
  2. Flagged-bet CLV — the same statistic, restricted to fixtures the
     model actually qualified. The model only demonstrates value if
     flagged CLV clears the baseline, not merely zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.validation.bootstrap import block_bootstrap_ci, bootstrap_ci
from src.validation.metrics import closing_line_value

INSUFFICIENT_SAMPLE_THRESHOLD = 10  # n <= this -> insufficient_sample=True


def two_price_clv(entry_odds: float, close_odds: float) -> float:
    """CLV = entry_odds / close_odds - 1. Positive means the entry price
    was better (higher) than where the market closed. A thin wrapper
    over src.validation.metrics.closing_line_value naming the two legs
    the way the rest of this module does."""
    return closing_line_value(entry_odds, close_odds)


def evaluate_clv_series(
    clv_values: np.ndarray,
    block_ids: np.ndarray | None = None,
    min_sample: int = INSUFFICIENT_SAMPLE_THRESHOLD,
    n_resamples: int = 2000,
    seed: int = 20260916,
) -> dict:
    """Summarize a series of per-match CLV values: n, mean, and a
    bootstrap CI (block bootstrap by `block_ids` — e.g. matchweek — if
    given, otherwise row-level).

    At or below `min_sample` observations, mean/ci are NaN and
    `insufficient_sample` is True instead of reporting a misleadingly
    precise-looking 0.00% — the no-silent-failure rule: a result you
    can't trust must not be indistinguishable from a real zero.
    """
    clv_values = np.asarray(clv_values, dtype=float)
    n = len(clv_values)

    if n <= min_sample:
        return {
            "n": n, "mean_clv": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"),
            "insufficient_sample": True,
        }

    mean_clv = float(np.mean(clv_values))
    if block_ids is not None:
        ci_lo, ci_hi = block_bootstrap_ci(
            clv_values, block_ids, lambda arr: float(np.mean(arr)), n_resamples=n_resamples, seed=seed,
        )
    else:
        ci_lo, ci_hi = bootstrap_ci(clv_values, lambda arr: float(np.mean(arr)), n_resamples=n_resamples, seed=seed)

    return {"n": n, "mean_clv": mean_clv, "ci_lo": ci_lo, "ci_hi": ci_hi, "insufficient_sample": False}


def evaluate_league_clv(
    df: pd.DataFrame,
    entry_col: str,
    close_col: str,
    flagged_mask: pd.Series,
    block_col: str | None = "matchweek",
    min_sample: int = INSUFFICIENT_SAMPLE_THRESHOLD,
    n_resamples: int = 2000,
    seed: int = 20260916,
) -> dict:
    """Baseline vs. flagged-bet CLV for one league (or any subset —
    caller controls grouping by pre-filtering `df`).

    `df` must already have both `entry_col` and `close_col` populated
    (drop/exclude rows lacking either before calling, with a logged
    exclusion reason — this function only computes the statistic, it
    doesn't decide what counts as evaluable).
    """
    priced = df[df[entry_col].notna() & df[close_col].notna()].copy()
    clv = priced[entry_col] / priced[close_col] - 1.0
    block_ids = priced[block_col].to_numpy() if block_col and block_col in priced.columns else None

    baseline = evaluate_clv_series(clv.to_numpy(), block_ids, min_sample, n_resamples, seed)

    flagged_priced = priced.loc[flagged_mask.reindex(priced.index, fill_value=False)]
    flagged_clv = clv.loc[flagged_priced.index]
    flagged_block_ids = (
        flagged_priced[block_col].to_numpy() if block_col and block_col in flagged_priced.columns else None
    )
    flagged = evaluate_clv_series(flagged_clv.to_numpy(), flagged_block_ids, min_sample, n_resamples, seed)

    return {"baseline": baseline, "flagged": flagged}

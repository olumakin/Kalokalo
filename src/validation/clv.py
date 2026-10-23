"""
Closing Line Value evaluation for the WP3 gate harness.

Three questions, all needed for the gate decision:
  1. Baseline CLV — across *every* fixture with both an entry and a
     closing price, regardless of whether the model flagged it. This is
     the market's own natural CLV distribution (should center near 0
     for a random/no-edge selection).
  2. Flagged-bet CLV — the same statistic, restricted to fixtures the
     model actually qualified. The model only demonstrates value if
     flagged CLV clears the baseline, not merely zero.
  3. Flagged-vs-baseline diff — whether the model's selection actually
     beats the naive "take the draw at entry odds on every fixture"
     strategy, not just zero. A league can show positive flagged CLV
     purely because the draw price drifts favorably across the board
     (positive baseline too) without the model's filtering adding
     anything; added as a permanent gate criterion after a WP3 review
     found exactly this in three of four leagues the gate had cleared
     on flagged CLV alone (see gate_report.compute_gate_decision).
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

    diff = _evaluate_clv_diff(
        priced, clv, flagged_mask, block_col, min_sample, n_resamples, seed, n_flagged=flagged["n"],
    )

    return {"baseline": baseline, "flagged": flagged, "diff": diff}


def _evaluate_clv_diff(
    priced: pd.DataFrame,
    clv: pd.Series,
    flagged_mask: pd.Series,
    block_col: str | None,
    min_sample: int,
    n_resamples: int,
    seed: int,
    n_flagged: int,
) -> dict:
    """Bootstrap CI for (flagged mean CLV - baseline mean CLV): does the
    model's selection beat naively taking the draw price on every
    priced fixture, not just beat zero.

    Resamples the *shared* priced population (flagged is a subset of
    baseline, not an independent sample) so each resample's flagged and
    baseline means are computed from the same drawn rows -- this
    respects the overlap between the two populations rather than
    treating them as independent, which would overstate the interval
    width.
    """
    if n_flagged <= min_sample:
        return {"mean_diff": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "insufficient_sample": True}

    resample_df = pd.DataFrame({
        "clv": clv.to_numpy(),
        "flagged": flagged_mask.reindex(priced.index, fill_value=False).to_numpy(),
    })
    mean_diff = float(
        resample_df.loc[resample_df["flagged"], "clv"].mean() - resample_df["clv"].mean()
    )

    def diff_stat(resampled: pd.DataFrame) -> float:
        baseline_mean = resampled["clv"].mean()
        flagged_rows = resampled.loc[resampled["flagged"]]
        flagged_mean = flagged_rows["clv"].mean() if len(flagged_rows) else np.nan
        return flagged_mean - baseline_mean

    block_ids = priced[block_col].to_numpy() if block_col and block_col in priced.columns else None
    if block_ids is not None:
        ci_lo, ci_hi = block_bootstrap_ci(resample_df, block_ids, diff_stat, n_resamples=n_resamples, seed=seed)
    else:
        ci_lo, ci_hi = bootstrap_ci(resample_df, diff_stat, n_resamples=n_resamples, seed=seed)

    return {"mean_diff": mean_diff, "ci_lo": ci_lo, "ci_hi": ci_hi, "insufficient_sample": False}

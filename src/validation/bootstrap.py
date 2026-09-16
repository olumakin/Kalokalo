"""
Seeded bootstrap confidence intervals for the WP3 evidence-pack harness.

`np.random.choice` only accepts 1-D input, so resampling *values*
directly (rather than *row indices*) breaks the moment a statistic
needs more than one column per resampled unit (e.g. ROI, which needs
both the outcome and the odds together). Every function here resamples
integer indices instead, which works uniformly for a 1-D array, a 2-D
array, or a DataFrame, and keeps paired columns aligned within each
resampled row.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

DEFAULT_SEED = 20260916


def _take(data, idx: np.ndarray):
    if isinstance(data, pd.DataFrame):
        return data.iloc[idx].reset_index(drop=True)
    arr = np.asarray(data)
    return arr[idx]


def bootstrap_ci(
    data, stat: Callable, n_resamples: int = 2000, alpha: float = 0.05, seed: int = DEFAULT_SEED,
) -> tuple[float, float]:
    """Percentile bootstrap CI for `stat(data)`.

    `data` may be a 1-D array, a 2-D array, or a DataFrame — resampling
    is always by row index, so `stat` sees the same row-aligned shape
    each time. Returns (nan, nan) for fewer than 2 observations rather
    than a degenerate [0, 0] interval that reads as a real result.
    """
    n = len(data)
    if n < 2:
        return float("nan"), float("nan")

    rng = np.random.default_rng(seed)
    stats = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        stats[i] = stat(_take(data, idx))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def block_bootstrap_ci(
    data, block_ids, stat: Callable, n_resamples: int = 2000, alpha: float = 0.05, seed: int = DEFAULT_SEED,
) -> tuple[float, float]:
    """Bootstrap CI resampling whole blocks (e.g. matchweeks) with
    replacement, rather than individual rows.

    Bets placed in the same round of matches share information (the
    market often moves together on shared news, injuries, weather) and
    the same closing-line snapshot timing — treating them as
    independent observations understates the true interval width.
    Resamples `n_blocks` blocks per iteration (with replacement) and
    concatenates them into one resample, so the resample size varies
    match-to-match but the block structure is preserved.
    """
    data = data if isinstance(data, pd.DataFrame) else pd.DataFrame({"value": np.asarray(data)})
    block_ids = np.asarray(block_ids)
    if len(data) != len(block_ids):
        raise ValueError("data and block_ids must be the same length")

    unique_blocks = np.unique(block_ids)
    n_blocks = len(unique_blocks)
    if n_blocks < 2:
        return float("nan"), float("nan")

    block_row_idx = {b: np.where(block_ids == b)[0] for b in unique_blocks}

    rng = np.random.default_rng(seed)
    stats = np.empty(n_resamples)
    for i in range(n_resamples):
        chosen_blocks = unique_blocks[rng.integers(0, n_blocks, n_blocks)]
        idx = np.concatenate([block_row_idx[b] for b in chosen_blocks])
        stats[i] = stat(_take(data, idx))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def paired_bootstrap_ci(
    a, b, stat_diff: Callable, n_resamples: int = 2000, alpha: float = 0.05, seed: int = DEFAULT_SEED,
) -> tuple[float, float]:
    """Bootstrap CI for a *paired* difference statistic (e.g. the
    log-loss edge: market log-loss minus model log-loss, per match).

    `a` and `b` must be the same length and row-aligned (the same match
    in both); resamples the shared index once per iteration so the
    pairing survives into each resample, then applies `stat_diff(a', b')`.
    A well-known use is testing whether one forecaster's loss is
    significantly lower than another's (a bootstrap analogue of
    Diebold-Mariano) without assuming a parametric loss distribution.
    """
    n = len(a)
    if n != len(b):
        raise ValueError("a and b must be the same length")
    if n < 2:
        return float("nan"), float("nan")

    rng = np.random.default_rng(seed)
    stats = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        stats[i] = stat_diff(_take(a, idx), _take(b, idx))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)

"""
R01: Chronological grid sweep for selecting per-league time-decay parameter xi.

Evaluates candidate xi values strictly within the training/validation window,
guaranteeing zero final-holdout contamination. Saves every trial to a reproducible
manifest (data/reports/xi_sweep_manifest.json).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.validation.clv import two_price_clv
from src.validation.gate_harness import assert_datetime_utc, run_league_evaluation
from src.validation.three_way import rps, three_way_log_loss

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XI_GRID = [0.003, 0.004, 0.005, 0.0065, 0.008, 0.010]


def sweep_league_xi(
    matches: pd.DataFrame,
    league: str,
    xi_grid: list[float] | None = None,
    split_ratio: float = 0.70,
    train_val_split_date: pd.Timestamp | str | None = None,
    min_train_matches: int = 150,
    retrain_every_days: int = 14,
    cache_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Perform chronological xi sweep for one league on tuning window only (R01).

    The matches DataFrame is partitioned chronologically:
    - Tuning window (past): used for walk-forward trial evaluation.
    - Holdout window (future): completely hidden and unread.
    """
    assert_datetime_utc(matches, "date")
    xi_grid = sorted(xi_grid or DEFAULT_XI_GRID)

    df_league = matches[matches["league"] == league].sort_values("date").reset_index(drop=True)
    if df_league.empty:
        return {
            "league": league,
            "status": "no_data",
            "selected_xi": None,
            "trials": [],
        }

    # Establish chronological split cutoff
    if train_val_split_date is not None:
        cutoff = pd.Timestamp(train_val_split_date)
        if cutoff.tz is None:
            cutoff = cutoff.tz_localize("UTC")
    else:
        split_idx = int(len(df_league) * split_ratio)
        cutoff = df_league.iloc[split_idx]["date"]

    tuning_df = df_league[df_league["date"] < cutoff].copy().reset_index(drop=True)
    if len(tuning_df) < min_train_matches:
        logger.warning(
            "League %s has only %d matches before cutoff %s (minimum %d required)",
            league, len(tuning_df), cutoff, min_train_matches,
        )
        return {
            "league": league,
            "status": "insufficient_history",
            "cutoff_date": str(cutoff),
            "tuning_matches": len(tuning_df),
            "selected_xi": None,
            "trials": [],
        }

    trials: list[dict[str, Any]] = []
    best_score = float("inf")
    selected_xi = xi_grid[0]

    for idx, xi_val in enumerate(xi_grid):
        logger.info("Evaluating league %s xi=%.4f (trial %d/%d)", league, xi_val, idx + 1, len(xi_grid))
        results, exclusions = run_league_evaluation(
            matches=tuning_df,
            league=league,
            xi=xi_val,
            min_train_matches=min_train_matches,
            retrain_every_days=retrain_every_days,
            cache_dir=cache_dir,
            run_id=f"sweep_{league}_{xi_val:.4f}",
        )

        n_eval = len(results)
        if n_eval == 0:
            trials.append({
                "trial_index": idx + 1,
                "xi": xi_val,
                "eval_matches": 0,
                "log_loss": None,
                "rps": None,
                "mean_clv": None,
                "converged_ratio": 0.0,
            })
            continue

        outcome_idx = results["actual_outcome_idx"]
        p_h, p_d, p_a = results["p_home"], results["p_draw"], results["p_away"]

        ll = float(three_way_log_loss(outcome_idx, p_h, p_d, p_a))
        r_score = float(rps(outcome_idx, p_h, p_d, p_a))

        flagged = results[results["qualified"] & results["entry_draw"].notna() & results["close_draw"].notna()]
        if not flagged.empty:
            mean_clv = float((flagged["entry_draw"] / flagged["close_draw"] - 1.0).mean())
        else:
            mean_clv = 0.0

        converged_ratio = float(results["converged"].mean())

        # Optimization criterion: Minimize Ranked Probability Score (RPS)
        if r_score < best_score:
            best_score = r_score
            selected_xi = xi_val

        trials.append({
            "trial_index": idx + 1,
            "xi": xi_val,
            "eval_matches": n_eval,
            "log_loss": round(ll, 4),
            "rps": round(r_score, 4),
            "mean_clv": round(mean_clv, 4),
            "converged_ratio": round(converged_ratio, 4),
        })

    return {
        "league": league,
        "status": "completed",
        "cutoff_date": str(cutoff),
        "tuning_matches": len(tuning_df),
        "selection_metric": "min_rps",
        "best_metric_value": round(best_score, 4) if best_score < float("inf") else None,
        "selected_xi": selected_xi,
        "trials": trials,
    }


def sweep_all_leagues(
    matches: pd.DataFrame,
    leagues: list[str],
    xi_grid: list[float] | None = None,
    split_ratio: float = 0.70,
    min_train_matches: int = 150,
    retrain_every_days: int = 14,
    output_manifest: Path | str = "data/reports/xi_sweep_manifest.json",
    cache_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute xi sweep for all given leagues and save manifest to disk (R01)."""
    manifest: dict[str, Any] = {
        "leagues": {},
        "xi_grid": sorted(xi_grid or DEFAULT_XI_GRID),
        "split_ratio": split_ratio,
    }

    for league in leagues:
        res = sweep_league_xi(
            matches=matches,
            league=league,
            xi_grid=xi_grid,
            split_ratio=split_ratio,
            min_train_matches=min_train_matches,
            retrain_every_days=retrain_every_days,
            cache_dir=cache_dir,
        )
        manifest["leagues"][league] = res

    out_path = Path(output_manifest)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Saved xi sweep manifest to %s", out_path)
    return manifest

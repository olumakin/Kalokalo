"""
Out-of-sample evaluation metrics for the walk-forward backtest.
Evaluated against de-vigged market consensus and Closing Line Value
rather than raw Brier score thresholds alone (PID Milestone 3).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def log_loss(y_true: np.ndarray, p_pred: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(np.asarray(p_pred, dtype=float), eps, 1 - eps)
    y = np.asarray(y_true, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier_score(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(p_pred, dtype=float)
    return float(np.mean((p - y) ** 2))


def calibration_curve(y_true: np.ndarray, p_pred: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"y": y_true, "p": p_pred})
    df["bin"] = pd.cut(df["p"], bins=np.linspace(0, 1, n_bins + 1), include_lowest=True)
    grouped = df.groupby("bin", observed=True).agg(
        predicted_mean=("p", "mean"),
        empirical_rate=("y", "mean"),
        count=("y", "size"),
    ).reset_index()
    return grouped


def roi_flat_stake(df: pd.DataFrame, stake: float = 1.0) -> float:
    """Flat-stake ROI over qualified bets: pnl / total staked."""
    bets = df[df["qualified"]]
    if bets.empty:
        return 0.0
    pnl = np.where(bets["actual_draw"], stake * (bets["odds_draw"] - 1), -stake)
    return float(pnl.sum() / (stake * len(bets)))


def roi_kelly_stake(df: pd.DataFrame, stake_col: str = "stake_pct") -> float:
    """Kelly-stake ROI over qualified bets: pnl / total staked (as bankroll fraction)."""
    bets = df[df["qualified"]]
    if bets.empty or stake_col not in bets.columns:
        return 0.0
    pnl = np.where(
        bets["actual_draw"],
        bets[stake_col] * (bets["odds_draw"] - 1),
        -bets[stake_col],
    )
    total_staked = bets[stake_col].sum()
    if total_staked == 0:
        return 0.0
    return float(pnl.sum() / total_staked)


def max_drawdown(cumulative_pnl: pd.Series) -> float:
    """Maximum peak-to-trough decline of a cumulative PnL series."""
    running_max = cumulative_pnl.cummax()
    drawdown = cumulative_pnl - running_max
    return float(drawdown.min())


def closing_line_value(bet_odds: float, closing_odds: float) -> float:
    """CLV = odds_bet / odds_closing - 1.

    Positive means the bet was placed at a better (higher) price than
    the eventual closing line — a leading indicator of a sharp bettor
    even before the match outcome is known.
    """
    if closing_odds <= 0:
        raise ValueError("closing_odds must be positive")
    return float(bet_odds / closing_odds - 1.0)

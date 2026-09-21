"""Walk-Forward Backtest — strict chronological out-of-sample validation. Admin only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from src.ingestion.historical import load_settings
from src.validation.backtest import WalkForwardBacktest
from src.validation.metrics import brier_score, calibration_curve, log_loss, max_drawdown, roi_flat_stake
from src.webapp.auth import require_admin

st.set_page_config(page_title="DVPE — Backtest", page_icon="🧪", layout="wide")
require_admin()

st.title("Walk-Forward Backtest")
st.caption(
    "For matchday T, the model is trained only on matches strictly before T — zero data leakage. "
    "Evaluated against de-vigged market consensus, not raw accuracy alone."
)

settings = st.session_state.get("settings") or load_settings()
matches = st.session_state.get("matches")

with st.sidebar:
    st.header("History")
    if matches is None or matches.empty:
        st.caption("No history loaded — run the pipeline on the **Matchday** page first.")
    else:
        st.caption(f"Using {len(matches)} matches loaded from the Matchday page.")

    st.header("Backtest settings")
    xi = st.number_input("Time-decay ξ", min_value=0.001, max_value=0.02, value=float(settings["model"]["xi_decay"]), step=0.001, format="%.4f")
    min_ev_pct = st.slider("Minimum EV to qualify (%)", 0.0, 15.0, float(settings["edge"]["min_ev"]) * 100, step=0.5)
    min_ev = min_ev_pct / 100
    rolling_window_days = st.slider("Rolling window (days)", 180, 1095, int(settings["model"]["rolling_window_days"]))
    retrain_every_days = st.slider(
        "Retrain cadence (days)", 1, 30, 7,
        help="Model is refit this often; predictions between refits still only use data strictly before the retrain date.",
    )
    run_backtest = st.button("Run walk-forward backtest", type="primary", use_container_width=True)

if matches is None or matches.empty:
    st.info("Generate or load a match history in the sidebar to run a backtest.")
    st.stop()

if run_backtest:
    with st.spinner("Running walk-forward simulation (refitting periodically, zero leakage)..."):
        bt = WalkForwardBacktest(
            min_matches=settings["model"]["min_matches_for_team_rating"],
            max_iter=settings["model"]["max_optimizer_iterations"],
            method=settings["model"]["optimizer_method"],
            xi=xi,
            rolling_window_days=rolling_window_days,
            retrain_every_days=retrain_every_days,
            devig_method=settings["devig"]["method"],
            min_ev=min_ev,
            min_train_matches=max(50, settings["model"]["min_matches_for_team_rating"] * 4),
        )
        results = bt.run(matches)
    st.session_state["backtest_results"] = results

results: pd.DataFrame = st.session_state.get("backtest_results", pd.DataFrame())

if results.empty:
    st.info("Configure settings and click **Run walk-forward backtest**.")
    st.stop()

qualified = results[results["qualified"]]
y_true = results["actual_draw"].astype(float)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Matches evaluated", len(results))
c2.metric("Log-loss", f"{log_loss(y_true, results['model_p_draw']):.4f}")
c3.metric("Brier score", f"{brier_score(y_true, results['model_p_draw']):.4f}")
c4.metric("Qualified bets", len(qualified))

if not qualified.empty:
    c5, c6, c7 = st.columns(3)
    hit_rate = qualified["actual_draw"].mean()
    c5.metric("Flat-stake ROI (qualified)", f"{roi_flat_stake(results) * 100:+.2f}%")
    c6.metric("Qualified draw hit rate", f"{hit_rate * 100:.1f}%")

    stake = 1.0
    pnl = np.where(qualified["actual_draw"], stake * (qualified["odds_draw"] - 1), -stake)
    cum_pnl = pd.Series(pnl, index=qualified["date"].values).cumsum()
    c7.metric("Max drawdown (flat stake)", f"{max_drawdown(cum_pnl):.2f}u")

    st.subheader("Cumulative flat-stake PnL — qualified bets only")
    st.line_chart(cum_pnl.rename("cumulative_pnl"))
else:
    st.info("No bets qualified at this EV threshold over this backtest window.")

st.subheader("Calibration — model P(Draw) vs. empirical draw rate")
calib = calibration_curve(y_true, results["model_p_draw"], n_bins=10).dropna()
if not calib.empty:
    chart_df = calib.set_index("predicted_mean")[["empirical_rate"]]
    st.line_chart(chart_df)
    st.caption("A well-calibrated model tracks the y=x diagonal (predicted probability ≈ empirical draw rate).")
    st.dataframe(calib, use_container_width=True)

with st.expander("Raw backtest predictions"):
    st.dataframe(results, use_container_width=True)
    st.download_button(
        "Download backtest results (CSV)",
        results.to_csv(index=False).encode("utf-8"),
        file_name="dvpe_backtest_results.csv",
        mime="text/csv",
    )

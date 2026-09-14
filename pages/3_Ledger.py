"""Prediction Ledger — audit trail and outcome reconciliation."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ingestion.historical import load_settings
from src.tracking.ledger import Ledger

st.set_page_config(page_title="DVPE — Ledger", page_icon="📒", layout="wide")
st.title("Prediction Ledger")
st.caption("Every prediction the pipeline has generated, qualified or not, for post-hoc calibration and CLV audit.")

settings = st.session_state.get("settings") or load_settings()
ledger = Ledger(settings["ledger"]["path"], fmt=settings["ledger"]["format"])
df = ledger.load()

if df.empty:
    st.info("No ledger entries yet. Run the pipeline on the **Matchday** page with 'Record predictions to ledger' checked.")
    st.stop()

df["timestamp"] = pd.to_datetime(df["timestamp"])
df["staked"] = df["stake_pct"].fillna(0) > 0
df["settled"] = df["actual_score"].notna()

# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    leagues = sorted(df["league"].dropna().unique().tolist())
    league_filter = st.multiselect("League", leagues, default=leagues)
    staked_only = st.checkbox("Staked bets only", value=False)
    settled_only = st.checkbox("Settled only", value=False)

filtered = df[df["league"].isin(league_filter)] if league_filter else df
if staked_only:
    filtered = filtered[filtered["staked"]]
if settled_only:
    filtered = filtered[filtered["settled"]]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Predictions logged", len(df))
c2.metric("Staked bets", int(df["staked"].sum()))
c3.metric("Settled", int(df["settled"].sum()))
settled = df[df["settled"] & df["pnl"].notna()]
c4.metric("Realized PnL (staked, settled)", f"{settled['pnl'].astype(float).sum():+.3f}u" if not settled.empty else "—")

st.subheader(f"Ledger entries ({len(filtered)})")
st.dataframe(filtered.sort_values("timestamp", ascending=False), use_container_width=True, height=420)
st.download_button(
    "Download ledger (CSV)", filtered.to_csv(index=False).encode("utf-8"),
    file_name="dvpe_ledger.csv", mime="text/csv",
)

st.divider()
st.subheader("Reconcile an outcome")
st.caption("Backfill the actual result for a previously logged prediction to close the audit loop.")

open_entries = df[~df["settled"]]
if open_entries.empty:
    st.caption("No open (unsettled) entries.")
else:
    # The ledger is append-only, so the same fixture can be logged more than
    # once (e.g. re-running the pipeline on the same demo fixture card).
    # Aggregate to one row per match_id so this form never hits an ambiguous
    # multi-row lookup.
    open_summary = open_entries.groupby("match_id").agg(
        staked=("staked", "any"), odds_draw=("odds_draw", "first"), stake_pct=("stake_pct", "max"),
    )
    match_id = st.selectbox(
        "Match",
        open_summary.index,
        format_func=lambda mid: f"{mid} (staked)" if open_summary.loc[mid, "staked"] else mid,
    )
    row = open_summary.loc[match_id]

    col1, col2, col3 = st.columns(3)
    with col1:
        home_goals = st.number_input("Home goals", min_value=0, max_value=15, value=1, step=1)
    with col2:
        away_goals = st.number_input("Away goals", min_value=0, max_value=15, value=1, step=1)
    with col3:
        closing_odds_draw = st.number_input(
            "Closing draw odds (optional, for CLV)", min_value=0.0, value=0.0, step=0.01,
            help="Leave at 0 to skip CLV calculation.",
        )

    if st.button("Record outcome", type="primary"):
        actual_draw = home_goals == away_goals
        stake_pct = float(row["stake_pct"]) if pd.notna(row["stake_pct"]) else 0.0
        odds_draw = float(row["odds_draw"])
        pnl = None
        if stake_pct > 0:
            pnl = stake_pct * (odds_draw - 1) if actual_draw else -stake_pct
        clv = None
        if closing_odds_draw > 0:
            clv = odds_draw / closing_odds_draw - 1
        ledger.update_outcome(
            match_id=match_id,
            actual_score=f"{home_goals}-{away_goals}",
            clv=clv,
            pnl=pnl,
        )
        st.success(f"Recorded {match_id}: {home_goals}-{away_goals}" + (f", pnl={pnl:+.4f}u" if pnl is not None else ""))
        st.rerun()

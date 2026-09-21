"""Prediction Ledger — audit trail and outcome reconciliation. Admin only."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.tracking.supabase_ledger import fetch_predictions, is_ledger_online, write_settlement
from src.webapp.auth import require_admin

st.set_page_config(page_title="DVPE — Ledger", page_icon="📒", layout="wide")
require_admin()

st.title("Prediction Ledger")
st.caption("Every prediction the pipeline has generated, for post-hoc calibration and CLV audit.")

ledger_online = is_ledger_online()
if not ledger_online:
    st.error("🔴 Ledger offline — Supabase is unreachable. Settlement is disabled until it recovers.")

df = fetch_predictions()

if df.empty:
    st.info("No ledger entries yet. Run the pipeline on the **Matchday** page.")
    st.stop()

df["created_at"] = pd.to_datetime(df["created_at"])
df["staked"] = df["stake_shadow"].fillna(0) > 0
df["settled"] = df["actual_home_goals"].notna()

# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    leagues = sorted(df["league"].dropna().unique().tolist())
    league_filter = st.multiselect("League", leagues, default=leagues)
    staked_only = st.checkbox("Shadow-staked only", value=False)

filtered = df[df["league"].isin(league_filter)] if league_filter else df
if staked_only:
    filtered = filtered[filtered["staked"]]

c1, c2, c3 = st.columns(3)
c1.metric("Predictions logged", len(df))
c2.metric("Shadow-staked", int(df["staked"].sum()))
c3.metric("Pipeline runs", df["run_id"].nunique())

st.subheader(f"Ledger entries ({len(filtered)})")
display_cols = [
    "created_at", "fixture_id", "league", "home_id", "away_id",
    "p_home", "p_draw", "p_away", "entry_draw", "market_p_draw", "ev_entry",
    "stake_shadow", "qualified" if "qualified" in filtered.columns else "run_id",
]
display_cols = [c for c in display_cols if c in filtered.columns]
st.dataframe(filtered[display_cols].sort_values("created_at", ascending=False), use_container_width=True, height=420)
st.download_button(
    "Download ledger (CSV)", filtered.to_csv(index=False).encode("utf-8"),
    file_name="dvpe_ledger.csv", mime="text/csv",
)

# --------------------------------------------------------------------------
# Settlement — appends to `settlements`, never touches `predictions`.
# --------------------------------------------------------------------------
st.divider()
st.subheader("Reconcile an outcome")
st.caption(
    "Backfill the actual result for a previously logged prediction. This appends a row to "
    "the `settlements` table — it never modifies the original prediction."
)

open_entries = df[~df["settled"]]
if open_entries.empty:
    st.caption("No open (unsettled) entries.")
else:
    fixture_ids = sorted(open_entries["fixture_id"].dropna().unique().tolist())
    fixture_id = st.selectbox("Fixture", fixture_ids)

    col1, col2, col3 = st.columns(3)
    with col1:
        home_goals = st.number_input("Home goals", min_value=0, max_value=15, value=1, step=1)
    with col2:
        away_goals = st.number_input("Away goals", min_value=0, max_value=15, value=1, step=1)
    with col3:
        closing_odds_draw = st.number_input(
            "Closing draw odds (optional, for CLV)", min_value=0.0, value=0.0, step=0.01,
            help="Leave at 0 to skip.",
        )

    if st.button("Record outcome", type="primary", disabled=not ledger_online):
        actual_result = "D" if home_goals == away_goals else ("H" if home_goals > away_goals else "A")
        try:
            settled_by = st.user.email if st.user.is_logged_in else None
        except Exception:  # noqa: BLE001
            settled_by = None
        ok = write_settlement(
            fixture_id=fixture_id,
            home_goals=int(home_goals),
            away_goals=int(away_goals),
            actual_result=actual_result,
            closing_odds_draw=closing_odds_draw or None,
            settled_by=settled_by,
        )
        if ok:
            st.success(f"Recorded {fixture_id}: {home_goals}-{away_goals}")
            st.rerun()
        else:
            st.error("Settlement write failed — Supabase may be unreachable. See logs.")

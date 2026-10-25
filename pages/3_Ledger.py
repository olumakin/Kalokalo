"""Prediction Ledger — audit trail and outcome reconciliation. Admin only."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.tracking.supabase_ledger import (
    fetch_predictions, historical_hit_rate, is_ledger_online, latest_settlement_by_fixture, write_settlement,
)
from src.webapp.auth import require_admin

st.set_page_config(page_title="DVPE — Ledger", page_icon="📒", layout="wide")
require_admin()

st.title("Prediction Ledger")
st.caption(
    "Every prediction the pipeline has generated, and how well 'High Draw Likelihood' calls "
    "have actually done once fixtures are settled below."
)

# Must match app.py's HIGH_TIE_P_DRAW -- the same threshold driving the
# "HIGH DRAW LIKELIHOOD" / "HIGH TIE POTENTIAL" badge on the Matchday page.
HIGH_TIE_P_DRAW = 0.275

ledger_online = is_ledger_online()
if not ledger_online:
    st.error("🔴 Ledger offline — Supabase is unreachable. Settlement is disabled until it recovers.")

df = fetch_predictions()

if df.empty:
    st.info("No ledger entries yet. Run the pipeline on the **Matchday** page.")
    st.stop()

df["created_at"] = pd.to_datetime(df["created_at"])
df["staked"] = df["stake_shadow"].fillna(0) > 0

# predictions.actual_home_goals is never populated by this app (settling a
# fixture only ever inserts into `settlements`, never touches `predictions`
# — see write_settlement's docstring) — "settled" has to come from whether
# a settlements row actually exists for this fixture, not from that column.
settlements = latest_settlement_by_fixture()
settled_fixture_ids = set(settlements["fixture_id"]) if not settlements.empty else set()
df["settled"] = df["fixture_id"].isin(settled_fixture_ids)

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
c1.metric("Predictions logged", len(df), help="Every fixture the pipeline has scored, across all runs.")
c2.metric(
    "Shadow-staked", int(df["staked"].sum()),
    help="Predictions that qualified for a shadow stake -- a what-if figure, not a validated recommendation.",
)
c3.metric("Pipeline runs", df["run_id"].nunique(), help="How many separate times the pipeline has been run.")

# --------------------------------------------------------------------------
# Historical hit rate — High Draw Likelihood forecast vs. actual result.
# Covers every settled fixture within the ledger window fetched above
# (fetch_predictions's `limit`, most recent first), not just whatever's
# currently on the Matchday page -- it grows as more fixtures settle.
# --------------------------------------------------------------------------
st.divider()
st.subheader("Historical hit rate: High Draw Likelihood vs. actual result")
st.caption(
    f"Of the fixtures flagged High Draw Likelihood (model draw probability ≥ {HIGH_TIE_P_DRAW:.0%}) "
    "that have since been settled, how many actually ended in a draw. A simple track record -- "
    "separate from the CLV-based testing that decides which leagues show as validated on the "
    "Matchday page."
)

hit_rate_df = historical_hit_rate(df, settlements, HIGH_TIE_P_DRAW)
if hit_rate_df.empty:
    st.info(
        "No settled High Draw Likelihood fixtures yet. Use **Reconcile an outcome** below to "
        "record results as they come in -- this fills in automatically from there."
    )
else:
    total_n = int(hit_rate_df["n"].sum())
    total_hits = int(hit_rate_df["hits"].sum())
    st.metric(
        "Hit rate", f"{total_hits / total_n:.1%}",
        help=f"{total_hits} of {total_n} settled High Draw Likelihood fixtures actually ended in a draw.",
    )
    display_hit_rate = hit_rate_df.rename(
        columns={"league": "League", "n": "Settled fixtures", "hits": "Actual draws", "hit_rate": "Hit rate"},
    )
    st.dataframe(
        display_hit_rate.style.format({"Hit rate": "{:.1%}"}), use_container_width=True, hide_index=True,
    )

st.subheader(f"Ledger entries ({len(filtered)})")
st.caption("Every scored fixture behind the numbers above, most recent first — the full audit trail.")
display_cols = [
    "created_at", "fixture_id", "league", "home_id", "away_id",
    "p_home", "p_draw", "p_away", "entry_draw", "market_p_draw", "ev_entry",
    "stake_shadow", "qualified" if "qualified" in filtered.columns else "run_id",
]
display_cols = [c for c in display_cols if c in filtered.columns]
friendly_names = {
    "created_at": "Logged at", "fixture_id": "Fixture", "league": "League", "home_id": "Home", "away_id": "Away",
    "p_home": "P(Home)", "p_draw": "P(Draw)", "p_away": "P(Away)", "entry_draw": "Entry odds (draw)",
    "market_p_draw": "Market P(Draw)", "ev_entry": "EV", "stake_shadow": "Shadow stake",
    "qualified": "Qualified", "run_id": "Run",
}
ledger_display = filtered[display_cols].sort_values("created_at", ascending=False).rename(columns=friendly_names)
pct_fmt = {friendly_names[c]: "{:.1%}" for c in ["p_home", "p_draw", "p_away", "market_p_draw", "stake_shadow"] if c in display_cols}
if "ev_entry" in display_cols:
    pct_fmt[friendly_names["ev_entry"]] = "{:+.1%}"
if "entry_draw" in display_cols:
    pct_fmt[friendly_names["entry_draw"]] = "{:.2f}"
st.dataframe(ledger_display.style.format(pct_fmt), use_container_width=True, height=420)
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
    "Backfill the actual result for a previously logged prediction. This is what feeds the "
    "hit-rate tracker above — it appends a row to the `settlements` table and never modifies "
    "the original prediction."
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

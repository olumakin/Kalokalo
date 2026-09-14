"""
DVPE Matchday Dashboard (Streamlit).

    streamlit run app.py
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ingestion.historical import load_settings
from src.pipeline import run
from src.tracking.ledger import Ledger

st.set_page_config(page_title="DVPE — Draw Value Prediction Engine", layout="wide")
st.title("Draw Value Prediction Engine — Football Big 5")
st.caption("Dixon-Coles model probabilities vs. de-vigged market consensus, EV-ranked.")

settings = load_settings()

with st.sidebar:
    st.header("Run settings")
    leagues = st.multiselect(
        "Leagues", options=list(settings["leagues"].keys()),
        default=list(settings["leagues"].keys()),
        format_func=lambda code: f"{code} — {settings['leagues'][code]}",
    )
    seasons_back = st.slider("Seasons of history", 1, 4, 2)
    fixtures_path = st.text_input("Fixture card CSV", value="data/fixtures/upcoming.csv")
    run_clicked = st.button("Run pipeline", type="primary")

if run_clicked:
    with st.spinner("Fitting Dixon-Coles model and scoring fixtures..."):
        predictions = run(fixtures_path, leagues, seasons_back)
    st.session_state["predictions"] = predictions

predictions: pd.DataFrame = st.session_state.get("predictions", pd.DataFrame())

if predictions.empty:
    st.info("Run the pipeline from the sidebar to score upcoming fixtures.")
else:
    qualified = predictions[predictions["qualified"]]
    c1, c2, c3 = st.columns(3)
    c1.metric("Fixtures scored", len(predictions))
    c2.metric("Qualified +EV draws", len(qualified))
    c3.metric("Total slate exposure", f"{qualified['stake_pct'].sum() * 100:.2f}%")

    st.subheader("Fixtures ranked by EV")

    def highlight_qualified(row):
        return ["background-color: #d4edda" if row["qualified"] else "" for _ in row]

    display_cols = [
        "date", "league", "home_team", "away_team",
        "model_p_draw", "market_p_draw", "odds_draw", "ev", "qualified", "stake_pct",
    ]
    styled = predictions[display_cols].style.apply(highlight_qualified, axis=1).format({
        "model_p_draw": "{:.3f}", "market_p_draw": "{:.3f}",
        "odds_draw": "{:.2f}", "ev": "{:+.3f}", "stake_pct": "{:.3%}",
    })
    st.dataframe(styled, use_container_width=True)

st.subheader("Prediction ledger (audit trail)")
ledger = Ledger(settings["ledger"]["path"], fmt=settings["ledger"]["format"])
ledger_df = ledger.load()
if ledger_df.empty:
    st.caption("No ledger entries yet.")
else:
    st.dataframe(ledger_df.sort_values("timestamp", ascending=False), use_container_width=True)

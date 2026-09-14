"""
DVPE Matchday Dashboard (Streamlit) — home page.

    streamlit run app.py

This sandbox has no outbound network access to football-data.co.uk, so
an offline "Demo data" mode (src/ingestion/demo_data.py) is available
alongside the live-download and CSV-upload paths, so the dashboard is
fully explorable without a network round-trip.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ingestion.demo_data import generate_demo_fixtures, generate_demo_matches
from src.ingestion.historical import load_settings
from src.ingestion.normalizer import build_display_names, load_team_mappings, normalize_dataframe
from src.ingestion.odds_feed import load_fixture_csv, normalize_fixture_dataframe
from src.pipeline import build_predictions, fit_model, load_historical_matches, record_ledger
from src.tracking.ledger import Ledger

st.set_page_config(page_title="DVPE — Draw Value Prediction Engine", page_icon="⚽", layout="wide")

MAX_CARDS = 10
QUALIFIED_BG = "background-color: rgba(34, 197, 94, 0.16)"


def highlight_qualified(row: pd.Series) -> list[str]:
    return [QUALIFIED_BG if row.get("qualified") else "" for _ in row]


@st.cache_data(show_spinner=False)
def _cached_demo_matches(league: str, n_teams: int, rounds: int, seed: int) -> pd.DataFrame:
    return generate_demo_matches(league=league, n_teams=n_teams, rounds=rounds, seed=seed)


@st.cache_data(show_spinner=False)
def _cached_demo_fixtures(league: str, n_teams: int, seed: int, n_fixtures: int, fixture_seed: int) -> pd.DataFrame:
    return generate_demo_fixtures(league=league, n_teams=n_teams, seed=seed, n_fixtures=n_fixtures, fixture_seed=fixture_seed)


@st.cache_data(show_spinner=False)
def _cached_historical_download(leagues: tuple[str, ...], seasons_back: int, _settings: dict) -> pd.DataFrame:
    return load_historical_matches(list(leagues), seasons_back, _settings)


settings = load_settings()
league_options = list(settings["leagues"].keys())

st.title("Draw Value Prediction Engine — Football Big 5")
st.caption("Dixon-Coles model probabilities vs. de-vigged market consensus, EV-ranked, Kelly-sized.")

# --------------------------------------------------------------------------
# Sidebar: data sources + run controls
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("1. Historical data")
    history_source = st.radio(
        "Source", ["Demo data (offline)", "Download (football-data.co.uk)", "Upload CSV"],
        help="This sandbox has no outbound network access, so Demo data is the reliable default.",
    )

    if history_source == "Demo data (offline)":
        demo_league = st.selectbox("League", league_options, format_func=lambda c: f"{c} — {settings['leagues'][c]}")
        demo_n_teams = st.slider("Teams", 6, 20, 10)
        demo_rounds = st.slider("Round-robins", 2, 8, 4, help="Each round-robin is a full home-and-away cycle.")
        demo_seed = st.number_input("Random seed", value=42, step=1)
    elif history_source == "Download (football-data.co.uk)":
        dl_leagues = st.multiselect(
            "Leagues", league_options, default=league_options,
            format_func=lambda c: f"{c} — {settings['leagues'][c]}",
        )
        dl_seasons_back = st.slider("Seasons of history", 1, 4, 2)
    else:
        uploaded_history = st.file_uploader(
            "Historical results CSV", type="csv",
            help="Columns: date, league, home_team, away_team, home_goals, away_goals, odds_home, odds_draw, odds_away",
        )

    st.header("2. Upcoming fixtures")
    fixture_source = st.radio(
        "Source", ["Demo fixtures (offline)", "Sample fixture card", "Upload CSV"],
    )
    if fixture_source == "Demo fixtures (offline)":
        demo_n_fixtures = st.slider("Number of fixtures", 4, 30, 12)
        demo_fixture_seed = st.number_input("Fixture random seed", value=3, step=1)
    elif fixture_source == "Sample fixture card":
        fixtures_path = st.text_input("Fixture CSV path", value="data/fixtures/upcoming.csv")
    else:
        uploaded_fixtures = st.file_uploader(
            "Fixture CSV", type="csv",
            help="Columns: date, league, home_team, away_team, odds_home, odds_draw, odds_away",
        )

    st.header("3. Run")
    record_to_ledger = st.checkbox("Record predictions to ledger", value=True)
    run_clicked = st.button("Run pipeline", type="primary", use_container_width=True)

# --------------------------------------------------------------------------
# Pipeline execution
# --------------------------------------------------------------------------
if run_clicked:
    with st.spinner("Loading historical data..."):
        if history_source == "Demo data (offline)":
            matches = _cached_demo_matches(demo_league, demo_n_teams, demo_rounds, int(demo_seed))
        elif history_source == "Download (football-data.co.uk)":
            matches = _cached_historical_download(tuple(dl_leagues), dl_seasons_back, settings)
        else:
            if uploaded_history is None:
                st.error("Upload a historical results CSV, or switch data source.")
                st.stop()
            raw = pd.read_csv(uploaded_history)
            matches = raw if set(["home_goals", "away_goals"]).issubset(raw.columns) else normalize_dataframe(raw)
            matches["date"] = pd.to_datetime(matches["date"])

    if matches.empty:
        st.error("No historical matches loaded — cannot fit the model. Try Demo data instead.")
        st.stop()

    with st.spinner("Fitting Dixon-Coles model..."):
        try:
            model = fit_model(matches, settings)
        except ValueError as exc:
            st.error(f"Model fit failed: {exc}")
            st.stop()

    with st.spinner("Loading fixtures..."):
        if fixture_source == "Demo fixtures (offline)":
            fixtures = _cached_demo_fixtures(
                demo_league if history_source == "Demo data (offline)" else league_options[0],
                demo_n_teams if history_source == "Demo data (offline)" else 10,
                int(demo_seed) if history_source == "Demo data (offline)" else 42,
                demo_n_fixtures, int(demo_fixture_seed),
            )
        elif fixture_source == "Sample fixture card":
            fixtures = load_fixture_csv(fixtures_path)
        else:
            if uploaded_fixtures is None:
                st.error("Upload a fixture CSV, or switch fixture source.")
                st.stop()
            fixtures = normalize_fixture_dataframe(pd.read_csv(uploaded_fixtures))

    if fixtures.empty:
        st.error("No fixtures loaded.")
        st.stop()

    predictions = build_predictions(fixtures, model, settings)
    if record_to_ledger:
        record_ledger(predictions, settings)

    st.session_state["model"] = model
    st.session_state["matches"] = matches
    st.session_state["predictions"] = predictions
    st.session_state["settings"] = settings

predictions: pd.DataFrame = st.session_state.get("predictions", pd.DataFrame())
model = st.session_state.get("model")

# --------------------------------------------------------------------------
# Results — a matchday decision view, not an optimizer debug dump.
# --------------------------------------------------------------------------
if predictions.empty:
    st.info("Configure a data source in the sidebar and click **Run pipeline** to score upcoming fixtures.")
else:
    qualified = predictions[predictions["qualified"]].sort_values("ev", ascending=False)

    # Model health is surfaced as a single unobtrusive flag, not raw
    # optimizer parameters — the full mu0/gamma/rho/converged readout
    # lives on the Model Diagnostics page.
    if model is not None and (model.fallback_used_ or not model.converged_):
        st.warning(
            "⚠️ Reduced model confidence on this run (optimizer fallback or non-convergence). "
            "See the **Model Diagnostics** page before acting on these picks."
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("Qualified Plays", f"{len(qualified)} Matches")
    c2.metric("Total Slate Exposure", f"{qualified['stake_pct'].sum():.1%}")
    c3.metric("Top Edge", f"+{qualified['ev'].max():.1%}" if not qualified.empty else "—")
    st.caption(f"Scanned {len(predictions)} fixtures on this slate.")

    st.divider()
    st.subheader("🎯 Matchday Value Picks")

    top_picks = qualified.head(MAX_CARDS)
    mappings = load_team_mappings()
    league_names = {lg: build_display_names(lg, mappings) for lg in predictions["league"].unique()}

    def team_name(code: str, league: str) -> str:
        return league_names.get(league, {}).get(code, code)

    if top_picks.empty:
        st.info("No fixtures meet the +3% EV threshold on this slate.")
    else:
        for _, row in top_picks.iterrows():
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])

                date_str = pd.to_datetime(row["date"]).strftime("%a, %b %d")
                home, away = team_name(row["home_team"], row["league"]), team_name(row["away_team"], row["league"])
                c1.markdown(f"**{home} vs {away}**")
                c1.caption(f"{settings['leagues'].get(row['league'], row['league'])} • {date_str}")

                c2.metric("Draw Odds", f"{row['odds_draw']:.2f}")
                c3.metric(
                    "Model vs Market",
                    f"{row['model_p_draw']:.1%}",
                    delta=f"+{(row['model_p_draw'] - row['market_p_draw']):.1%}",
                )
                c4.metric("Edge (EV)", f"+{row['ev']:.1%}")
                c5.metric("Stake", f"{row['stake_pct']:.2%}")

                with st.expander("Show tactical metrics"):
                    st.write(
                        f"Projected xG: **{home}** ({row['xg_home']:.2f}) — **{away}** ({row['xg_away']:.2f})"
                    )

    with st.expander(f"Show full fixture list ({len(predictions)} scanned, including non-qualified)"):
        display_cols = [
            "date", "league", "home_team", "away_team", "xg_home", "xg_away",
            "model_p_draw", "market_p_draw", "odds_draw", "ev", "qualified", "stake_pct",
        ]
        styled = predictions[display_cols].style.apply(highlight_qualified, axis=1).format({
            "date": lambda d: pd.to_datetime(d).strftime("%d %b %Y"),
            "xg_home": "{:.2f}", "xg_away": "{:.2f}",
            "model_p_draw": "{:.1%}", "market_p_draw": "{:.1%}",
            "odds_draw": "{:.2f}", "ev": "{:+.1%}", "stake_pct": "{:.2%}",
        })
        st.dataframe(styled, use_container_width=True, height=min(60 + 35 * len(predictions), 600))

    st.download_button(
        "Download predictions (CSV)",
        predictions.to_csv(index=False).encode("utf-8"),
        file_name="dvpe_predictions.csv",
        mime="text/csv",
    )

st.divider()
st.subheader("Recent ledger entries")
ledger = Ledger(settings["ledger"]["path"], fmt=settings["ledger"]["format"])
ledger_df = ledger.load()
if ledger_df.empty:
    st.caption("No ledger entries yet — run the pipeline with 'Record predictions to ledger' checked.")
else:
    st.dataframe(ledger_df.sort_values("timestamp", ascending=False).head(10), use_container_width=True)
    st.caption("Full audit trail, filters, and outcome reconciliation are on the **Ledger** page.")

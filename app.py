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

# Value-rating tiers for qualified plays, relative to the 3% EV qualification
# floor (config/settings.yaml: edge.min_ev) — not absolute magic numbers.
EXCEPTIONAL_EV = 0.15
HIGH_EV = 0.08

CARD_CSS = """
<style>
    div[data-testid="stVerticalBlockBorderWrapper"]:has(div.match-card-marker) {
        border-radius: 14px;
        background: linear-gradient(145deg, #131722, #181e2b);
        border: 1px solid #232d3f;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
        transition: all 0.2s ease-in-out;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(div.match-card-marker):hover {
        border-color: #3b82f6;
        box-shadow: 0 6px 24px rgba(59, 130, 246, 0.15);
        transform: translateY(-3px);
    }
    .value-badge {
        color: white;
        font-weight: 700;
        font-size: 11px;
        letter-spacing: 0.5px;
        padding: 4px 10px;
        border-radius: 20px;
        text-transform: uppercase;
        white-space: nowrap;
    }
    .badge-tier-1 {
        background: linear-gradient(135deg, #059669, #10b981);
        box-shadow: 0 0 10px rgba(16, 185, 129, 0.3);
    }
    .badge-tier-2 { background: linear-gradient(135deg, #2563eb, #3b82f6); }
    .badge-tier-3 { background: linear-gradient(135deg, #b45309, #f59e0b); }
    .payout-box {
        background: rgba(16, 185, 129, 0.08);
        border-left: 3px solid #10b981;
        padding: 8px 12px;
        border-radius: 6px;
        margin-top: 10px;
        font-size: 13px;
        color: #e2e8f0;
    }
</style>
"""


def highlight_qualified(row: pd.Series) -> list[str]:
    return [QUALIFIED_BG if row.get("qualified") else "" for _ in row]


def value_rating(ev: float) -> tuple[str, str]:
    """Map raw EV to a layman value-rating badge (css class, label)."""
    if ev >= EXCEPTIONAL_EV:
        return "badge-tier-1", "EXCEPTIONAL VALUE"
    if ev >= HIGH_EV:
        return "badge-tier-2", "STRONG VALUE"
    return "badge-tier-3", "FAIR VALUE"


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

st.markdown(CARD_CSS, unsafe_allow_html=True)

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
# Header — title + an unobtrusive model-health pill (not a jargon-dump
# banner; the full mu0/gamma/rho/converged readout lives on the Model
# Diagnostics page). Rendered here, after `model` is resolved from
# session_state, so it reflects the run that was just triggered below
# rather than lagging a step behind it.
# --------------------------------------------------------------------------
c_title, c_status = st.columns([3, 1])
with c_title:
    st.title("⚽ Matchday Draw Finder")
    st.caption("Quantitative signals uncovering undervalued draw outcomes across Big 5 leagues.")
with c_status:
    if model is not None and (model.fallback_used_ or not model.converged_):
        st.markdown(
            "<div style='text-align:right; padding-top:15px;'>"
            "<span title='Optimizer fell back to an independent Poisson model — see Model Diagnostics for details' "
            "style='background:#334155; color:#94a3b8; padding:4px 10px; border-radius:12px; font-size:12px;'>"
            "⚡ Poisson Fallback Active</span></div>",
            unsafe_allow_html=True,
        )

# --------------------------------------------------------------------------
# Results — a matchday decision view, not an optimizer debug dump.
# --------------------------------------------------------------------------
if predictions.empty:
    st.info("Configure a data source in the sidebar and click **Run pipeline** to score upcoming fixtures.")
else:
    qualified = predictions[predictions["qualified"]].sort_values("ev", ascending=False)

    c1, c2, c3 = st.columns(3)
    c1.metric("Qualified Plays", f"{len(qualified)} Matches")
    c2.metric("Total Suggested Stake", f"{qualified['stake_pct'].sum():.1%}")
    c3.metric("Top Value", f"+{qualified['ev'].max():.1%}" if not qualified.empty else "—")
    st.caption(f"Scanned {len(predictions)} fixtures on this slate.")

    st.divider()
    st.subheader("🎯 Matchday Value Picks")

    mappings = load_team_mappings()
    league_names = {lg: build_display_names(lg, mappings) for lg in predictions["league"].unique()}

    def team_name(code: str, league: str) -> str:
        return league_names.get(league, {}).get(code, code)

    # Control panel: bankroll + sort + how many cards, so "Recommended
    # Stake" below can show a dollar amount, not just an abstract percentage.
    c_filter1, c_filter2, c_filter3 = st.columns([2, 2, 2])
    with c_filter1:
        bankroll = st.number_input("Your Total Bankroll ($)", min_value=50, max_value=100_000, value=1000, step=50)
    with c_filter2:
        sort_choice = st.selectbox(
            "Sort Order", ["Earliest Match First", "Highest Return First", "Highest Model Confidence"],
        )
    with c_filter3:
        max_cards = st.slider("Matches Displayed", min_value=2, max_value=MAX_CARDS, value=6)

    if sort_choice == "Earliest Match First":
        top_picks = qualified.sort_values("date", ascending=True).head(max_cards)
    elif sort_choice == "Highest Return First":
        top_picks = qualified.sort_values("odds_draw", ascending=False).head(max_cards)
    else:
        top_picks = qualified.sort_values("ev", ascending=False).head(max_cards)

    if top_picks.empty:
        st.info("No standout draw value opportunities on this slate.")
    else:
        rows = list(top_picks.iterrows())
        for i in range(0, len(rows), 2):
            pair = rows[i:i + 2]
            cols = st.columns(2)
            for col, (_, row) in zip(cols, pair):
                with col, st.container(border=True):
                    st.markdown('<div class="match-card-marker"></div>', unsafe_allow_html=True)

                    badge_class, badge_text = value_rating(row["ev"])
                    date_str = pd.to_datetime(row["date"]).strftime("%a, %b %d")
                    league_full = settings["leagues"].get(row["league"], row["league"])
                    st.markdown(
                        f"<div style='display:flex; justify-content:space-between; align-items:center;'>"
                        f"<span style='color:#8b949e; font-size:13px;'>{league_full} • {date_str}</span>"
                        f"<span class='value-badge {badge_class}'>{badge_text}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    home = team_name(row["home_team"], row["league"])
                    away = team_name(row["away_team"], row["league"])
                    st.markdown(
                        f"<h3 style='margin:0 0 12px 0; font-size:1.25rem;'>{home} "
                        f"<span style='color:#64748b;'>vs</span> {away}</h3>",
                        unsafe_allow_html=True,
                    )

                    # odds_draw is decimal odds — the return multiplier per $1
                    # staked (Total Return = Stake x odds_draw), not a fixed
                    # payout tied to any particular stake size.
                    odds = float(row["odds_draw"])
                    stake_pct = float(row["stake_pct"])  # already a bankroll fraction, e.g. 0.025 = 2.5%
                    stake_dollars = round(bankroll * stake_pct, 2)
                    total_return = round(stake_dollars * odds, 2)
                    profit = round(total_return - stake_dollars, 2)

                    m1, m2 = st.columns(2)
                    m1.metric("Odds Multiplier", f"{odds:.2f}x")
                    m2.metric(
                        "Recommended Stake", f"${stake_dollars:,.0f}",
                        help=f"{stake_pct:.1%} of your ${bankroll:,.0f} bankroll",
                    )

                    st.markdown(
                        f"<div class='payout-box'>💰 Stake <strong>${stake_dollars:,.2f}</strong> to win "
                        f"<strong>${total_return:,.2f}</strong> "
                        f"<span style='color:#10b981; font-weight:600;'>(+${profit:,.2f} profit)</span></div>",
                        unsafe_allow_html=True,
                    )

                    with st.expander("Technical model breakdown"):
                        st.caption(
                            f"Estimated draw probability: {row['model_p_draw']:.1%} | "
                            f"Market implied: {row['market_p_draw']:.1%}"
                        )
                        st.caption(
                            f"Projected xG: {home} ({row['xg_home']:.2f}) vs {away} ({row['xg_away']:.2f})"
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

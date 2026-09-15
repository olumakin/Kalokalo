"""
DVPE Matchday Dashboard (Streamlit) — home page.

    streamlit run app.py

This sandbox has no outbound network access to football-data.co.uk, so
an offline "Demo data" mode (src/ingestion/demo_data.py) is available
alongside the live-download and CSV-upload paths, so the dashboard is
fully explorable without a network round-trip.
"""
from __future__ import annotations

import os
import uuid

import pandas as pd
import streamlit as st

from src.ingestion.demo_data import generate_demo_matches
from src.ingestion.historical import load_settings
from src.ingestion.normalizer import build_display_names, load_team_mappings, normalize_dataframe
from src.ingestion.odds_feed import (
    FIXTURE_SOURCE_FREE_SCHEDULE,
    FIXTURE_SOURCE_LIVE_ODDS,
    get_upcoming_fixtures,
)
from src.ingestion.sources import (
    BLEND_CONSENSUS,
    BLEND_STRICT,
    SOURCE_FOOTBALL_DATA,
    SOURCE_UNDERSTAT,
    load_and_blend_sources,
)
from src.pipeline import build_predictions, fit_model, load_historical_matches, record_ledger
from src.tracking.ledger import Ledger
from src.tracking.supabase_ledger import get_supabase_client, prediction_row_from_pipeline, write_predictions

st.set_page_config(page_title="Matchday Score Predictor | Big 5 Leagues", page_icon="⚽", layout="wide")

MAX_CARDS = 10
QUALIFIED_BG = "background-color: rgba(34, 197, 94, 0.16)"

# Draw-likelihood badge threshold on the real model's P(draw) — not an
# absolute magic number: the PID's +EV qualification floor is 3% edge
# over market, a different question from "is this draw just likely."
# 27.5% is comfortably above the Big-5 baseline draw rate (~24-26%).
HIGH_TIE_P_DRAW = 0.275

CARD_CSS = """
<style>
    .stApp {
        background-color: #0b0f19;
        color: #f1f5f9;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 16px;
        background: linear-gradient(145deg, #111827, #1e293b);
        border: 1px solid #334155;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
        padding: 6px;
        transition: transform 0.2s ease, border-color 0.2s ease;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: #38bdf8;
        transform: translateY(-3px);
    }
    .league-pill {
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        color: #94a3b8;
        text-transform: uppercase;
    }
    .badge-draw-high {
        background: rgba(16, 185, 129, 0.2);
        color: #34d399;
        border: 1px solid #059669;
        padding: 3px 10px;
        border-radius: 9999px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.05em;
    }
    .badge-draw-med {
        background: rgba(59, 130, 246, 0.2);
        color: #60a5fa;
        border: 1px solid #2563eb;
        padding: 3px 10px;
        border-radius: 9999px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.05em;
    }
    .badge-forecast-only {
        background: #334155;
        color: #f8fafc;
        padding: 3px 10px;
        border-radius: 9999px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.05em;
    }
    .score-box {
        background: rgba(15, 23, 42, 0.8);
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 10px 14px;
        text-align: center;
        margin: 12px 0;
    }
    .score-primary {
        font-size: 1.6rem;
        font-weight: 800;
        color: #f8fafc;
        letter-spacing: 2px;
    }
    .score-runnerup {
        font-size: 0.8rem;
        color: #94a3b8;
        margin-top: 2px;
    }
</style>
"""


def highlight_qualified(row: pd.Series) -> list[str]:
    return [QUALIFIED_BG if row.get("qualified") else "" for _ in row]


@st.cache_data(show_spinner=False)
def _cached_demo_matches(league: str, n_teams: int, rounds: int, seed: int) -> pd.DataFrame:
    return generate_demo_matches(league=league, n_teams=n_teams, rounds=rounds, seed=seed)


@st.cache_data(show_spinner=False)
def _cached_historical_download(leagues: tuple[str, ...], seasons_back: int, _settings: dict) -> pd.DataFrame:
    return load_historical_matches(list(leagues), seasons_back, _settings)


@st.cache_data(show_spinner=False)
def _cached_blend_sources(
    selected_sources: tuple[str, ...], leagues: tuple[str, ...], seasons_back: int, _settings: dict, blend_mode: str,
) -> pd.DataFrame:
    return load_and_blend_sources(list(selected_sources), list(leagues), seasons_back, _settings, blend_mode=blend_mode)


@st.cache_data(show_spinner=False)
def _cached_upcoming_fixtures(leagues: tuple[str, ...], api_key: str) -> tuple[pd.DataFrame, str]:
    return get_upcoming_fixtures(list(leagues), api_key=api_key or None)


def _resolve_odds_api_key() -> str:
    """ODDS_API_KEY from the environment, then Streamlit secrets if a
    secrets.toml is configured — st.secrets raises if none exists at
    all, so that lookup is guarded rather than assumed available."""
    key = os.environ.get("ODDS_API_KEY", "")
    if key:
        return key
    try:
        return st.secrets.get("ODDS_API_KEY", "")
    except Exception:
        return ""


def _resolve_rg_url() -> str:
    """Configurable Responsible Gambling resource link — env/secrets
    override, defaulting to BeGambleAware."""
    url = os.environ.get("RG_URL", "")
    if url:
        return url
    try:
        return st.secrets.get("RG_URL", "https://www.begambleaware.org/")
    except Exception:
        return "https://www.begambleaware.org/"


settings = load_settings()
league_options = list(settings["leagues"].keys())

st.markdown(CARD_CSS, unsafe_allow_html=True)

st.warning(
    f"**NOT FINANCIAL ADVICE.** This system is under technical validation. Outputs are "
    f"forecasting research only, not a recommendation to place a bet. "
    f"[Responsible Gambling Resources]({_resolve_rg_url()})"
)

# --------------------------------------------------------------------------
# Sidebar: data sources + run controls
# --------------------------------------------------------------------------
with st.sidebar:
    st.caption(f"⚠️ Forecast research only — not financial advice. [Responsible Gambling]({_resolve_rg_url()})")
    st.header("1. Historical & Market Data Sources")

    # Demo vs. online is a mode (a synthetic generator vs. real ingestion —
    # not a blendable "source"), so it stays a single checkbox rather than
    # forcing a radio pick just to get to the source checkboxes below.
    use_demo_history = st.checkbox(
        "Use offline demo data", value=True, key="src_check_demo",
        help="This sandbox has no outbound network access, so demo data is the reliable default. "
             "Uncheck to select real historical sources to blend.",
    )

    # Defaults so every branch below leaves these defined, regardless of
    # which path is actually taken.
    selected_sources: list[str] = []
    blend_mode = BLEND_CONSENSUS
    fit_on_xg = False
    src_custom_csv = False

    if use_demo_history:
        demo_league = st.selectbox(
            "League", league_options, format_func=lambda c: f"{c} — {settings['leagues'][c]}", key="demo_league_select",
        )
        demo_n_teams = st.slider("Teams", 6, 20, 10, key="demo_n_teams_slider")
        demo_rounds = st.slider(
            "Round-robins", 2, 8, 4, help="Each round-robin is a full home-and-away cycle.", key="demo_rounds_slider",
        )
        demo_seed = st.number_input("Random seed", value=42, step=1, key="demo_seed_input")
    else:
        st.caption("Select data sources to cross-analyze (tick all that apply):")
        src_football_data = st.checkbox(
            "football-data.co.uk (Match Logs & Closing Odds)", value=True, key="src_check_fd",
            help="Historical results, scores, and baseline bookmaker closing odds. The only source "
                 "here with actual match results — always the base of the blend.",
        )
        src_understat = st.checkbox(
            "Understat (xG & Shot Quality)", value=True, key="src_check_understat",
            help="Match-level Expected Goals to stabilize attack/defense ratings alongside raw goals.",
        )
        src_custom_csv = st.checkbox("Upload Custom CSV instead", value=False, key="src_check_csv")

        if src_custom_csv:
            uploaded_history = st.file_uploader(
                "Historical results CSV", type="csv", key="uploaded_history_file",
                help="Columns: date, league, home_team, away_team, home_goals, away_goals, odds_home, odds_draw, odds_away",
            )
        else:
            if src_football_data:
                selected_sources.append(SOURCE_FOOTBALL_DATA)
            if src_understat:
                selected_sources.append(SOURCE_UNDERSTAT)
            if not selected_sources:
                st.warning("⚠️ Select at least one data source, or use Upload Custom CSV.")

            dl_leagues = st.multiselect(
                "Active Leagues", league_options, default=league_options,
                format_func=lambda c: f"{c} — {settings['leagues'][c]}", key="hist_leagues_multiselect",
            )
            dl_seasons_back = st.slider("Seasons of history", 1, 4, 2, key="hist_seasons_slider")

            if src_understat:
                blend_mode = st.radio(
                    "Data Combination Strategy", [BLEND_CONSENSUS, BLEND_STRICT], index=0, key="blend_mode_radio",
                    help="Consensus keeps every base match (gaps where a source has no data). "
                         "Strict keeps only matches every selected source covers.",
                )
                fit_on_xg = st.checkbox(
                    "Fit Dixon-Coles on blended xG instead of raw goals scored",
                    value=False, key="fit_on_xg_checkbox",
                    help="Uses Understat's shot-based xG as the Poisson target instead of final-score "
                         "goals — a lower-variance proxy for attacking/defensive quality. Approximate: "
                         "xG isn't literally Poisson count data (see DixonColesModel.fit docstring). "
                         "Off by default — the PID's model is defined on actual goals.",
                )

    st.header("2. Live Market Feed")
    st.caption("Upcoming fixtures are fetched automatically — no source to pick.")
    odds_api_key = _resolve_odds_api_key()
    if odds_api_key:
        st.success("🟢 Live Odds API configured")
    else:
        odds_api_key = st.text_input(
            "Odds API Key (optional)", type="password", key="odds_api_key_input",
            help="Enter your API key, or set ODDS_API_KEY in your environment or Streamlit secrets. "
                 "Free tier: 500 requests/month, live odds only (no historical endpoint).",
        )
        st.caption(
            "🔵 Default mode: free football-data.co.uk feed active. If the key is missing, "
            "invalid, or rate-limited, this is used automatically — no error, no blank screen."
        )

    st.header("3. Run")
    record_to_ledger = st.checkbox("Record predictions to ledger", value=True)
    run_clicked = st.button("Run pipeline", type="primary", use_container_width=True)

# --------------------------------------------------------------------------
# Pipeline execution
# --------------------------------------------------------------------------
if run_clicked:
    with st.spinner("Loading historical data..."):
        if use_demo_history:
            matches = _cached_demo_matches(demo_league, demo_n_teams, demo_rounds, int(demo_seed))
        elif src_custom_csv:
            if uploaded_history is None:
                st.error("Upload a historical results CSV, or switch data source.")
                st.stop()
            raw = pd.read_csv(uploaded_history)
            matches = raw if set(["home_goals", "away_goals"]).issubset(raw.columns) else normalize_dataframe(raw)
            matches["date"] = pd.to_datetime(matches["date"])
        else:
            if not selected_sources:
                st.error("Select at least one data source, or use Upload Custom CSV.")
                st.stop()
            if not dl_leagues:
                st.error("Select at least one league to blend.")
                st.stop()
            matches = _cached_blend_sources(
                tuple(selected_sources), tuple(dl_leagues), dl_seasons_back, settings, blend_mode,
            )

    if matches.empty:
        st.error("No historical matches loaded — cannot fit the model. Try Demo data instead.")
        st.stop()

    with st.spinner("Fitting Dixon-Coles model..."):
        goal_cols = ("home_xg", "away_xg") if fit_on_xg else ("home_goals", "away_goals")
        try:
            model = fit_model(matches, settings, goal_columns=goal_cols)
        except ValueError as exc:
            st.error(f"Model fit failed: {exc}")
            st.stop()

    with st.spinner("Fetching upcoming fixtures..."):
        # Automatic three-tier chain, no source to pick: live consensus
        # odds (needs a key) -> free football-data.co.uk weekly schedule
        # (no key) -> bundled sample fixture card (always available).
        # get_upcoming_fixtures never raises purely because a remote tier
        # is unreachable — every run in this sandbox hits the final tier,
        # since there's no outbound network access here at all.
        fixtures, fixture_source_used = _cached_upcoming_fixtures(tuple(league_options), odds_api_key)

    if fixtures.empty:
        st.error("No fixtures available — live feed, free schedule, and the bundled fixture card all returned nothing.")
        st.stop()

    predictions = build_predictions(fixtures, model, settings)
    if record_to_ledger:
        try:
            record_ledger(predictions, settings)
            st.session_state["ledger_status"] = "OK"
        except Exception as exc:  # noqa: BLE001 — surface in the UI, never fail the run over a logging write
            st.session_state["ledger_failed_writes"] = st.session_state.get("ledger_failed_writes", 0) + len(predictions)
            st.session_state["ledger_status"] = f"write error ({exc})"

        # Durable remote copy (PID Phase 0) — additive, never blocks the
        # local ledger or the run itself. Skips cleanly if Supabase isn't
        # configured; get_supabase_client/write_predictions never raise.
        run_id = str(uuid.uuid4())
        sb_client = get_supabase_client()
        if sb_client is None:
            st.session_state["supabase_ledger_status"] = "Not configured"
        else:
            sb_rows = [
                prediction_row_from_pipeline(row, run_id, model, settings, fixture_source_used)
                for _, row in predictions.iterrows()
            ]
            sb_failed = write_predictions(sb_rows, client=sb_client)
            prior_failed = st.session_state.get("supabase_ledger_failed_writes", 0)
            st.session_state["supabase_ledger_failed_writes"] = prior_failed + sb_failed
            st.session_state["supabase_ledger_status"] = "OK" if sb_failed == 0 else f"{sb_failed} row(s) failed this run"

    st.session_state["fixture_source_used"] = fixture_source_used
    st.session_state["model"] = model
    st.session_state["matches"] = matches
    st.session_state["predictions"] = predictions
    st.session_state["settings"] = settings

with st.sidebar:
    st.divider()
    st.markdown("### System Health")
    _ledger_status = st.session_state.get("ledger_status", "Not yet run")
    _failed_writes = st.session_state.get("ledger_failed_writes", 0)
    if _failed_writes > 0 or "error" in _ledger_status.lower():
        st.error(f"Local ledger: {_ledger_status} | Failed writes: {_failed_writes}")
    else:
        st.success(f"Local ledger: {_ledger_status} | Failed writes: 0")

    _sb_status = st.session_state.get("supabase_ledger_status", "Not configured")
    _sb_failed = st.session_state.get("supabase_ledger_failed_writes", 0)
    if _sb_failed > 0 or "error" in _sb_status.lower() or "fail" in _sb_status.lower():
        st.error(f"Supabase ledger: {_sb_status} | Failed writes: {_sb_failed}")
    elif "not configured" in _sb_status.lower():
        st.info(f"Supabase ledger: {_sb_status}")
    else:
        st.success(f"Supabase ledger: {_sb_status} | Failed writes: 0")

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
    st.title("⚽ Matchday Score Predictor")
    st.caption("Algorithmic scoreline forecasts and deadlock analysis across Europe's top divisions.")
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
# Results — Matchday Score Predictor: real fitted scorelines, xG, and
# outcome probabilities from the Dixon-Coles model (src/models/simulator.py),
# not a synthetic/random engine. Every fixture on the slate is shown, not
# just qualified +EV ones — this is a score predictor first; the PID's
# draw-value/EV logic still runs underneath and surfaces as a small
# "+EV PLAY" badge on cards that clear the qualification bar.
# --------------------------------------------------------------------------
if predictions.empty:
    st.info("Configure a data source in the sidebar and click **Run pipeline** to score upcoming fixtures.")
else:
    mappings = load_team_mappings()
    league_names = {lg: build_display_names(lg, mappings) for lg in predictions["league"].unique()}

    def team_name(code: str, league: str) -> str:
        return league_names.get(league, {}).get(code, code)

    _feed_notes = {
        FIXTURE_SOURCE_LIVE_ODDS: "Live Odds API (Consensus)",
        FIXTURE_SOURCE_FREE_SCHEDULE: "football-data.co.uk (Free)",
    }
    feed_label = _feed_notes.get(st.session_state.get("fixture_source_used"), "Bundled sample fixture card")

    st.markdown(
        f"""
            <div style="display:flex; justify-content:flex-end; margin-bottom:10px;">
                <span style="background:#1e293b; color:#38bdf8; border:1px solid #334155;
                             padding:4px 12px; border-radius:9999px; font-size:12px; font-weight:600;">
                    Feed: {feed_label}
                </span>
            </div>
        """,
        unsafe_allow_html=True,
    )

    # High-level summary row
    col1, col2, col3 = st.columns(3)
    col1.metric("Matches Analyzed", f"{len(predictions)} Fixtures")
    avg_draw_prob = predictions["model_p_draw"].mean()
    col2.metric("Average Tie Likelihood", f"{avg_draw_prob:.1%}")
    top_tie = predictions.sort_values("model_p_draw", ascending=False).iloc[0]
    col3.metric(
        "Top Tie Candidate",
        f"{team_name(top_tie['home_team'], top_tie['league'])} vs {team_name(top_tie['away_team'], top_tie['league'])}",
    )

    st.write("")

    # Control panel: sort + how many cards.
    c_sort, c_limit = st.columns([2, 1])
    with c_sort:
        sort_mode = st.selectbox(
            "Order Matches By", ["Earliest Kickoff Date", "Highest Tie Likelihood", "Highest Total xG"],
        )
    with c_limit:
        card_limit = st.slider("Matches Displayed", min_value=2, max_value=MAX_CARDS, value=6)

    if sort_mode == "Earliest Kickoff Date":
        display_df = predictions.sort_values("date", ascending=True)
    elif sort_mode == "Highest Tie Likelihood":
        display_df = predictions.sort_values("model_p_draw", ascending=False)
    else:
        display_df = predictions.assign(total_xg=predictions["xg_home"] + predictions["xg_away"]).sort_values(
            "total_xg", ascending=False
        )
    display_df = display_df.head(card_limit)

    rows = list(display_df.iterrows())
    for i in range(0, len(rows), 2):
        pair = rows[i:i + 2]
        cols = st.columns(2)
        for col, (_, match) in zip(cols, pair):
            with col, st.container(border=True):
                is_high_tie = match["model_p_draw"] >= HIGH_TIE_P_DRAW
                tie_badge_class = "badge-draw-high" if is_high_tie else "badge-draw-med"
                tie_badge_label = "HIGH TIE POTENTIAL" if is_high_tie else "MODERATE TIE CHANCE"

                date_str = pd.to_datetime(match["date"]).strftime("%a, %b %d")
                league_full = settings["leagues"].get(match["league"], match["league"])
                st.markdown(
                    f"<div style='display:flex; justify-content:space-between; align-items:center;'>"
                    f"<span class='league-pill'>{league_full} • {date_str}</span>"
                    f"<span>"
                    f"<span class='{tie_badge_class}'>{tie_badge_label}</span>"
                    f"<span class='badge-forecast-only' style='margin-left:6px;'>FORECAST ONLY</span>"
                    f"</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                home = team_name(match["home_team"], match["league"])
                away = team_name(match["away_team"], match["league"])
                st.markdown(
                    f"<h3 style='margin:8px 0 2px 0;'>{home} "
                    f"<span style='color:#64748b; font-weight:400;'>vs</span> {away}</h3>",
                    unsafe_allow_html=True,
                )

                st.markdown(
                    f"""
                        <div class="score-box">
                            <div style="font-size:0.72rem; text-transform:uppercase; color:#38bdf8; font-weight:700; letter-spacing:0.05em;">Most Likely Final Score</div>
                            <div class="score-primary">{match['top_score']}</div>
                            <div class="score-runnerup">Alternative: <strong>{match['alt_score']}</strong> ({match['alt_score_prob']:.0%} chance)</div>
                        </div>
                    """,
                    unsafe_allow_html=True,
                )

                g1, g2, g3 = st.columns(3)
                g1.metric("Home xG", f"{match['xg_home']:.2f}")
                g2.metric("Draw Chance", f"{match['model_p_draw']:.1%}")
                g3.metric("Away xG", f"{match['xg_away']:.2f}")

                p_h = round(match["model_p_home"] * 100)
                p_d = round(match["model_p_draw"] * 100)
                p_a = round(match["model_p_away"] * 100)
                st.caption(f"Forecast: **{home}** {p_h}% | **Draw** {p_d}% | **{away}** {p_a}%")
                st.progress(min(max(p_h / 100.0, 0.0), 1.0))

                if match["qualified"]:
                    with st.expander("Betting value analysis"):
                        st.caption(
                            f"Model draw probability {match['model_p_draw']:.1%} vs. market "
                            f"{match['market_p_draw']:.1%} → **{match['ev']:+.1%} EV**, "
                            f"{match['stake_pct']:.1%} of bankroll recommended (Fractional Kelly, capped)."
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

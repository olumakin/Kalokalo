"""
DVPE Matchday Dashboard (Streamlit) — public home page.

    streamlit run app.py

Phase 0 (revised): this page is public. A public visitor gets a
read-only view of the most recently published predictions, fetched
from Supabase — this page never fits a model, calls a data feed, or
writes anything for anyone who isn't a signed-in admin
(src.webapp.auth.is_admin). Admin controls (data source selection, Run
pipeline) render only inside the `if admin:` sidebar block below.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from src.ingestion.historical import load_settings
from src.ingestion.normalizer import build_display_names, load_team_mappings
from src.ingestion.odds_feed import (
    FIXTURE_SOURCE_FREE_SCHEDULE,
    FIXTURE_SOURCE_LIVE_ODDS,
    FIXTURE_SOURCE_SAMPLE_CARD,
    get_upcoming_fixtures,
)
from src.models.simulator import build_score_matrix, top_scorelines
from src.pipeline import build_predictions, fit_model, load_historical_matches, record_predictions_to_supabase
from src.tracking.supabase_ledger import fetch_gate_status, fetch_latest_predictions, is_ledger_online
from src.webapp.auth import is_admin

st.set_page_config(page_title="Matchday Score Predictor | Big 5 Leagues", page_icon="⚽", layout="wide")

MAX_CARDS = 10

# Draw-likelihood badge threshold on the real model's P(draw) — not an
# absolute magic number: comfortably above the Big-5 baseline draw rate
# (~24-26%).
HIGH_TIE_P_DRAW = 0.275

_FEED_LABELS = {
    FIXTURE_SOURCE_LIVE_ODDS: "Live Odds API (Consensus)",
    FIXTURE_SOURCE_FREE_SCHEDULE: "football-data.co.uk (Free)",
    FIXTURE_SOURCE_SAMPLE_CARD: "Bundled sample fixture card",
}

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


def _format_age(delta: pd.Timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


@st.cache_data(show_spinner=False, ttl="2m")
def _cached_latest_predictions() -> pd.DataFrame:
    return fetch_latest_predictions()


@st.cache_data(show_spinner=False, ttl="6h")
def _cached_historical_download(leagues: tuple[str, ...], seasons_back: int, _settings: dict) -> pd.DataFrame:
    return load_historical_matches(list(leagues), seasons_back, _settings)


@st.cache_data(show_spinner=False, ttl="15m")
def _cached_upcoming_fixtures(leagues: tuple[str, ...], api_key: str) -> tuple[pd.DataFrame, str]:
    return get_upcoming_fixtures(list(leagues), api_key=api_key or None)


settings = load_settings()
league_options = list(settings["leagues"].keys())

st.markdown(CARD_CSS, unsafe_allow_html=True)

# --------------------------------------------------------------------------
# Age gate — must confirm before any content (public or admin) renders,
# remembered for the browser session only (st.session_state).
# --------------------------------------------------------------------------
if not st.session_state.get("age_confirmed"):
    st.warning(
        "**You must be 18 or older to use this site.** This system is under technical "
        "validation. Outputs are forecasting research only — **NOT FINANCIAL ADVICE** and "
        "not a recommendation to place a bet. "
        f"[Responsible Gambling Resources]({_resolve_rg_url()})"
    )
    if st.button("I am 18 or older — continue"):
        st.session_state["age_confirmed"] = True
        st.rerun()
    st.stop()

st.warning(
    f"**NOT FINANCIAL ADVICE.** This system is under technical validation. Outputs are "
    f"forecasting research only, not a recommendation to place a bet. "
    f"[Responsible Gambling Resources]({_resolve_rg_url()})"
)

admin = is_admin()

# --------------------------------------------------------------------------
# Sidebar: admin-only pipeline controls. A public visitor sees a login
# prompt and nothing else here — no data source, no run control.
# --------------------------------------------------------------------------
run_clicked = False
with st.sidebar:
    st.caption(f"⚠️ Forecast research only — not financial advice. [Responsible Gambling]({_resolve_rg_url()})")

    if not admin:
        st.info("Admin sign-in required to run the pipeline.")
        try:
            st.login()
        except Exception as exc:  # noqa: BLE001 — [auth] not configured yet
            st.caption("Admin login isn't configured yet.")
            st.caption(f"Debug (temporary, Phase 0 rollout): {exc}")
    else:
        try:
            st.button("Log out", on_click=st.logout, use_container_width=True)
        except Exception:  # noqa: BLE001
            pass

        st.header("1. Historical Data")
        st.caption("football-data.co.uk match logs and closing odds.")
        dl_leagues = st.multiselect(
            "Active Leagues", league_options, default=league_options,
            format_func=lambda c: f"{c} — {settings['leagues'][c]}", key="hist_leagues_multiselect",
        )
        dl_seasons_back = st.slider("Seasons of history", 1, 4, 2, key="hist_seasons_slider")

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
        ledger_online = is_ledger_online()
        if not ledger_online:
            st.error("🔴 Ledger offline — Supabase is unreachable. Run is disabled until it recovers.")
        run_clicked = st.button(
            "Run pipeline", type="primary", use_container_width=True, disabled=not ledger_online,
        )

# --------------------------------------------------------------------------
# Admin pipeline execution — unreachable for anyone `admin` is False for,
# since `run_clicked` can only be True inside the `if admin:` sidebar
# block above.
# --------------------------------------------------------------------------
if admin and run_clicked:
    with st.spinner("Loading historical data..."):
        if not dl_leagues:
            st.error("Select at least one league.")
            st.stop()
        matches = _cached_historical_download(tuple(dl_leagues), dl_seasons_back, settings)

    if matches.empty:
        st.error("No historical matches loaded — cannot fit the model.")
        st.stop()

    with st.spinner("Fitting Dixon-Coles model..."):
        try:
            model = fit_model(matches, settings)
        except ValueError as exc:
            st.error(f"Model fit failed: {exc}")
            st.stop()

    with st.spinner("Fetching upcoming fixtures..."):
        # Scoped to whichever leagues actually ended up in `matches` — a
        # league the model never saw historical data for has every team
        # "unseen", so predict() would silently default both teams to
        # generic league-median ratings rather than a real prediction.
        fixture_leagues = sorted(matches["league"].unique().tolist())
        fixtures, fixture_source_used = _cached_upcoming_fixtures(tuple(fixture_leagues), odds_api_key)

    if fixtures.empty:
        st.error("No fixtures available — live feed, free schedule, and the bundled fixture card all returned nothing.")
        st.stop()

    predictions_out = build_predictions(fixtures, model, settings)
    result = record_predictions_to_supabase(predictions_out, model, settings, price_source=fixture_source_used)
    if result["failed"] > 0:
        st.error(f"Supabase write: {result['status']}")
    else:
        st.success(f"Recorded {len(predictions_out)} predictions to Supabase (run {result['run_id'][:8]}...).")

    # Session-only handoff to the admin-only Model Diagnostics / Backtest
    # pages — never used by the public read path below.
    st.session_state["model"] = model
    st.session_state["matches"] = matches
    st.session_state["settings"] = settings

    _cached_latest_predictions.clear()
    st.rerun()

# --------------------------------------------------------------------------
# Public content — always renders for everyone, reads only from Supabase.
# --------------------------------------------------------------------------
c_title, _c_spacer = st.columns([3, 1])
with c_title:
    st.title("⚽ Matchday Score Predictor")
    st.caption("Algorithmic scoreline forecasts and deadlock analysis across Europe's top divisions.")

predictions = _cached_latest_predictions()

if predictions.empty:
    st.info("No predictions published yet. Check back after the next admin run.")
else:
    mappings = load_team_mappings()
    predictions = predictions.copy()
    predictions["match_date"] = pd.to_datetime(predictions["match_date"])
    predictions["created_at"] = pd.to_datetime(predictions["created_at"], utc=True)

    league_names = {lg: build_display_names(lg, mappings) for lg in predictions["league"].dropna().unique()}

    def team_name(code: str, league: str) -> str:
        return league_names.get(league, {}).get(code, code)

    available_leagues = sorted(predictions["league"].dropna().unique().tolist())
    selected_leagues = st.multiselect(
        "Active Competitions", options=available_leagues, default=available_leagues,
        format_func=lambda c: settings["leagues"].get(c, f"Unknown code: {c}"),
        key="active_competitions_filter",
        help="Filters the published predictions below.",
    )

    filtered = predictions[predictions["league"].isin(selected_leagues)] if selected_leagues else predictions.iloc[0:0]

    if not selected_leagues:
        st.info("Select at least one competition above to view matches.")
    elif filtered.empty:
        st.warning("No published fixtures for the selected competition(s).")
    else:
        feed_label = _FEED_LABELS.get(filtered.iloc[0].get("entry_source"), "Unknown feed")
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

        col1, col2, col3 = st.columns(3)
        col1.metric("Matches Analyzed", f"{len(filtered)} Fixtures")
        col2.metric("Average Tie Likelihood", f"{filtered['p_draw'].mean():.1%}")
        top_tie = filtered.sort_values("p_draw", ascending=False).iloc[0]
        col3.metric(
            "Top Tie Candidate",
            f"{team_name(top_tie['home_id'], top_tie['league'])} vs {team_name(top_tie['away_id'], top_tie['league'])}",
        )

        st.write("")

        c_sort, c_limit = st.columns([2, 1])
        with c_sort:
            sort_mode = st.selectbox(
                "Order Matches By", ["Earliest Kickoff Date", "Highest Tie Likelihood", "Highest Total xG"],
            )
        with c_limit:
            card_limit = st.slider("Matches Displayed", min_value=2, max_value=MAX_CARDS, value=6)

        if sort_mode == "Earliest Kickoff Date":
            display_df = filtered.sort_values("match_date", ascending=True)
        elif sort_mode == "Highest Tie Likelihood":
            display_df = filtered.sort_values("p_draw", ascending=False)
        else:
            display_df = filtered.assign(total_xg=filtered["lambda_val"] + filtered["mu_val"]).sort_values(
                "total_xg", ascending=False
            )
        display_df = display_df.head(card_limit)

        # Shadow stakes are admin-only, and only for a league whose gate
        # is explicitly approved (status='proceed' and approved_at set).
        # With the seeded defaults this is empty for every league.
        gate_status = fetch_gate_status() if admin else {}
        now_utc = pd.Timestamp.now(tz="UTC")

        rows = list(display_df.iterrows())
        for i in range(0, len(rows), 2):
            pair = rows[i:i + 2]
            cols = st.columns(2)
            for col, (_, match) in zip(cols, pair):
                with col, st.container(border=True):
                    is_high_tie = match["p_draw"] >= HIGH_TIE_P_DRAW
                    tie_badge_class = "badge-draw-high" if is_high_tie else "badge-draw-med"
                    tie_badge_label = "HIGH TIE POTENTIAL" if is_high_tie else "MODERATE TIE CHANCE"

                    date_str = pd.to_datetime(match["match_date"]).strftime("%a, %b %d")
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

                    home = team_name(match["home_id"], match["league"])
                    away = team_name(match["away_id"], match["league"])
                    st.markdown(
                        f"<h3 style='margin:8px 0 2px 0;'>{home} "
                        f"<span style='color:#64748b; font-weight:400;'>vs</span> {away}</h3>",
                        unsafe_allow_html=True,
                    )

                    # Scorelines are derived at read time from the stored
                    # lambda/mu/rho, not written to the DB themselves —
                    # they're a pure function of those three numbers, so
                    # storing them separately would just be a second copy
                    # that could drift.
                    matrix = build_score_matrix(match["lambda_val"], match["mu_val"], match["rho_val"])
                    (h1, a1, p1), (h2, a2, p2) = top_scorelines(matrix, n=2)
                    st.markdown(
                        f"""
                            <div class="score-box">
                                <div style="font-size:0.72rem; text-transform:uppercase; color:#38bdf8; font-weight:700; letter-spacing:0.05em;">Most Likely Final Score</div>
                                <div class="score-primary">{h1}-{a1}</div>
                                <div class="score-runnerup">Alternative: <strong>{h2}-{a2}</strong> ({p2:.0%} chance)</div>
                            </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    g1, g2, g3 = st.columns(3)
                    g1.metric("Home xG", f"{match['lambda_val']:.2f}")
                    g2.metric("Draw Chance", f"{match['p_draw']:.1%}")
                    g3.metric("Away xG", f"{match['mu_val']:.2f}")

                    p_h = round(match["p_home"] * 100)
                    p_d = round(match["p_draw"] * 100)
                    p_a = round(match["p_away"] * 100)
                    st.caption(f"Forecast: **{home}** {p_h}% | **Draw** {p_d}% | **{away}** {p_a}%")
                    st.progress(min(max(p_h / 100.0, 0.0), 1.0))

                    age = now_utc - match["created_at"]
                    st.caption(f"Priced {_format_age(age)} ago · {match.get('entry_source') or 'unknown source'}")

                    if admin:
                        league_gate = gate_status.get(match["league"], {})
                        if league_gate.get("status") == "proceed" and league_gate.get("approved_at"):
                            with st.expander("Shadow: not validated"):
                                stake = match.get("stake_shadow")
                                ev = match.get("ev_entry")
                                ev_str = f"EV {ev:+.1%}" if pd.notna(ev) else "EV n/a"
                                stake_str = f", stake {stake:.1%} of bankroll" if pd.notna(stake) else ""
                                st.caption(f"{ev_str}{stake_str} — shadow figure, not a validated recommendation.")

        public_cols = [
            "match_date", "league", "home_id", "away_id", "p_home", "p_draw", "p_away",
        ]
        public_cols = [c for c in public_cols if c in filtered.columns]
        with st.expander(f"Show full fixture list ({len(filtered)} scanned)"):
            styled = filtered[public_cols].rename(columns={"home_id": "home_team", "away_id": "away_team"}).style.format({
                "match_date": lambda d: pd.to_datetime(d).strftime("%d %b %Y"),
                "p_home": "{:.1%}", "p_draw": "{:.1%}", "p_away": "{:.1%}",
            })
            st.dataframe(styled, use_container_width=True, height=min(60 + 35 * len(filtered), 600))

        st.download_button(
            "Download predictions (CSV)",
            filtered[public_cols].to_csv(index=False).encode("utf-8"),
            file_name="dvpe_predictions.csv",
            mime="text/csv",
        )

st.divider()
try:
    st.page_link("pages/4_Terms.py", label="Terms of Use (draft)")
except Exception:  # noqa: BLE001 — older Streamlit without page_link
    st.caption("See the Terms of Use page in the sidebar.")

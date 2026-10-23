"""Model Diagnostics — Dixon-Coles fitted parameters and team ratings.

One row per league, not one shared fit: the live pipeline fits each
league separately (src.pipeline.fit_model), matching what the WP3
gate-evaluation harness validates (src.validation.gate_harness
.run_league_evaluation) — a joint fit across the Big 5 pinned one
shared baseline scoring rate and home advantage across leagues that
genuinely differ on both, and was a real convergence risk besides.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.webapp.auth import require_admin

st.set_page_config(page_title="DVPE — Model Diagnostics", page_icon="📊", layout="wide")
require_admin()

st.title("Model Diagnostics")

models = st.session_state.get("models")
matches = st.session_state.get("matches")

if not models:
    st.info("Run the pipeline on the **Matchday** page first — this page inspects the last fitted models.")
    st.stop()

st.subheader("Per-league fit summary")
summary = pd.DataFrame([
    {
        "league": league,
        "intercept (μ₀)": model.mu0_,
        "home advantage (γ)": model.gamma_,
        "ρ": model.rho_,
        "teams": len(model.teams_),
        "regularized": len(model.regularized_teams_),
        "converged": model.converged_,
        "fallback": model.fallback_used_,
    }
    for league, model in sorted(models.items())
])
st.dataframe(
    summary.style.format({"intercept (μ₀)": "{:+.3f}", "home advantage (γ)": "{:+.3f}", "ρ": "{:+.3f}"}),
    use_container_width=True, hide_index=True,
)

fallback_leagues = summary[summary["fallback"]]["league"].tolist()
if fallback_leagues:
    st.warning(
        f"**{', '.join(fallback_leagues)}**: the optimizer did not converge with ρ free within the "
        "iteration budget, so this league's fit fell back to an independent Poisson model (ρ=0), "
        "per the PID's failsafe convergence rule. Predictions for these leagues are using no "
        "low-score correlation correction."
    )

st.divider()
st.subheader("Team ratings")
league = st.selectbox("League", sorted(models.keys()))
model = models[league]

st.caption(
    "α = attack strength, β = defense weakness (higher β = more goals conceded). "
    "Both are on a log-goals scale, zero-mean across eligible teams (sum-to-zero identifiability constraint). "
    "Regularized teams are pinned at the league median (0, 0)."
)

ratings = pd.DataFrame({
    "team": list(model.teams_),
    "attack": [model.alpha_[t] for t in model.teams_],
    "defense": [model.beta_[t] for t in model.teams_],
})
ratings["regularized"] = ratings["team"].isin(model.regularized_teams_)
ratings["reference_team"] = ratings["team"] == model.reference_team_
if matches is not None and not matches.empty:
    league_matches = matches[matches["league"] == league]
    appearances = pd.concat([league_matches["home_team"], league_matches["away_team"]]).value_counts()
    ratings["matches"] = ratings["team"].map(appearances).fillna(0).astype(int)
ratings = ratings.sort_values("attack", ascending=False).reset_index(drop=True)

left, right = st.columns([3, 2])
with left:
    st.dataframe(
        ratings.style.format({"attack": "{:+.3f}", "defense": "{:+.3f}"}),
        use_container_width=True, height=520,
    )
with right:
    st.caption("Attack strength by team")
    st.bar_chart(ratings.set_index("team")["attack"])
    st.caption("Defense weakness by team (lower is better)")
    st.bar_chart(ratings.set_index("team")["defense"])

st.caption(
    "Attack vs. defense — top-right teams score freely and defend well; "
    "bottom-left teams struggle on both ends."
)
st.scatter_chart(ratings, x="attack", y="defense", color="regularized")

"""Model Diagnostics — Dixon-Coles fitted parameters and team ratings."""
from __future__ import annotations

import pandas as pd
import streamlit as st

st.set_page_config(page_title="DVPE — Model Diagnostics", page_icon="📊", layout="wide")
st.title("Model Diagnostics")

model = st.session_state.get("model")
matches = st.session_state.get("matches")

if model is None:
    st.info("Run the pipeline on the **Matchday** page first — this page inspects the last fitted model.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Intercept (μ₀)", f"{model.mu0_:.3f}")
c2.metric("Home advantage (γ)", f"{model.gamma_:.3f}", help="Home team's expected goals get an exp(γ) multiplier.")
c3.metric("Low-score correlation (ρ)", f"{model.rho_:.3f}")
c4.metric("Teams fitted", len(model.teams_))

status_cols = st.columns(3)
status_cols[0].metric("Converged", "Yes" if model.converged_ else "No")
status_cols[1].metric("Fallback to independent Poisson", "Yes" if model.fallback_used_ else "No")
status_cols[2].metric("Regularized (new/promoted) teams", len(model.regularized_teams_))

if model.fallback_used_:
    st.warning(
        "The optimizer did not converge with ρ free within the iteration budget, so this fit "
        "fell back to an independent Poisson model (ρ=0), per the PID's failsafe convergence rule."
    )

st.divider()
st.subheader("Team ratings")
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
    appearances = pd.concat([matches["home_team"], matches["away_team"]]).value_counts()
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

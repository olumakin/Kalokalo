# Draw Value Prediction Engine (DVPE) — Football Big 5

Automated quantitative pipeline that identifies mispriced draw outcomes
across the top five European football leagues (EPL, La Liga, Serie A,
Bundesliga, Ligue 1). Compares a time-decayed Dixon-Coles bivariate
Poisson goal model against de-vigged market consensus odds and sizes
positions with Fractional Kelly under dual exposure caps.

See the Project Initiation Document for full modeling detail, risk
framework, and phased delivery plan.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Run the end-to-end pipeline against a fixture card:

```bash
python -m src.pipeline --fixtures data/fixtures/upcoming.csv --leagues E0,SP1,I1,D1,F1 --seasons-back 2
```

Launch the matchday dashboard:

```bash
streamlit run app.py
```

Run the test suite:

```bash
pytest
```

## Project layout

```
config/          Hyperparameters and canonical team-name mappings
data/            Cached historical results, fixture cards, prediction ledger
src/ingestion/   Historical results + fixture/odds ingestion, team normalization
src/models/      Dixon-Coles fitting engine and 10x10 scoreline simulator
src/analytics/   De-vigging (multiplicative / Shin) and EV / Kelly sizing
src/validation/  Strict walk-forward backtest and evaluation metrics
src/tracking/    Append-only prediction ledger
src/pipeline.py  End-to-end CLI entry point
app.py           Streamlit matchday dashboard
```

## Guardrails

- **Zero data leakage:** the walk-forward backtest (`src/validation/backtest.py`)
  trains only on matches strictly before the prediction date.
- **Failsafe convergence:** if the Dixon-Coles optimizer does not converge
  within `max_optimizer_iterations`, it falls back to an independent
  Poisson model (`rho = 0`) and flags the fit (`fallback_used_`).
- **Dual exposure caps:** every qualifying stake is capped at 2.5% of
  bankroll per match, then the full daily slate is scaled down (if
  needed) to stay within an 8.0% aggregate cap.

## Status

Phases 1-5 of the PID are implemented as a working MVP:

- [x] Historical ingestion + canonical team-identity normalization
- [x] Dixon-Coles parameterization with time decay and promoted-team regularization
- [x] Strict walk-forward backtest + log-loss / Brier / ROI / CLV metrics
- [x] De-vig (multiplicative + Shin) and Fractional Kelly sizing with risk caps
- [x] Persistent prediction ledger + Streamlit matchday dashboard

Not yet wired: a live odds-provider integration (`src/ingestion/odds_feed.py`
has the interface; MVP source is the curated `data/fixtures/` CSV) and the
`xi` decay grid search itself, which is exposed as a config surface
(`model.xi_grid` in `config/settings.yaml`) for the validation harness to
sweep.

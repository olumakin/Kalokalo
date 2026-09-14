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

Launch the web dashboard:

```bash
streamlit run app.py
```

Run the test suite:

```bash
pytest
```

## Web interface

A four-page Streamlit app:

- **Matchday** (`app.py`) — pick historical data sources (offline demo
  generator, or football-data.co.uk + Understat to blend, or a CSV
  upload), run the pipeline, and see fixtures ranked by EV with xG,
  model vs. market draw probability, and Kelly stake, qualified bets
  highlighted. Upcoming fixtures are fetched automatically through a
  three-tier chain, with no source to pick: live consensus odds from
  The Odds API if a key is set, then the free weekly football-data.co.uk
  fixture sheet (no key needed), then a bundled sample fixture card as a
  last resort. If nothing on the slate clears the +3% EV bar, the
  closest misses are still shown — visually distinct (dashed border,
  "BELOW THRESHOLD" badge, no stake/payout box) so they read as context,
  never as a recommendation.
- **Model Diagnostics** (`pages/1_Model_Diagnostics.py`) — fitted
  hyperparameters (μ₀, γ, ρ), convergence/fallback status, and per-team
  attack/defense ratings with charts.
- **Backtest** (`pages/2_Backtest.py`) — runs the strict walk-forward
  engine over the loaded history and reports log-loss, Brier score,
  flat-stake ROI, max drawdown, and a calibration curve.
- **Ledger** (`pages/3_Ledger.py`) — full audit trail with filters, and a
  form to backfill actual results (closing the PnL/CLV loop).

This sandbox has no outbound network access to football-data.co.uk, so
`src/ingestion/demo_data.py` generates a plausible offline match history
(real team codes, a fitted-model-vs-noisy-market dynamic) so the app is
fully explorable without a live data source — "Use offline demo data"
is checked by default in the sidebar. Fixtures always come from
`get_upcoming_fixtures` (`src/ingestion/odds_feed.py`), trying live odds,
then the free schedule, then `data/fixtures/upcoming.csv` in order — every
run in this sandbox hits that last tier, since none of the remote sources
are reachable here. Because the sample card's real Big 5 teams (Arsenal,
Real Madrid, Juventus, ...) mostly don't overlap the demo history's
randomly-sampled team pool, the out-of-the-box run in this sandbox
usually lands on the below-threshold "closest misses" view rather than a
qualified pick — showing that is the point (a working example of the
tool declining to recommend a stake), not a bug.

## Multi-source data blending

football-data.co.uk alone gives goals and closing odds but no
shot-quality signal — a 1-0 can easily have been a 2-2 on underlying
chances. The Matchday sidebar's "Blend online sources" option
(`src/ingestion/sources.py`) can merge it with:

- **Understat** (`src/ingestion/understat_xg.py`) — match-level xG,
  scraped from an embedded JSON blob on Understat's league pages (no
  public API exists). Merged onto the base results by (date, league,
  home_team, away_team), in either mode:
  - *Blended Consensus* (default) — keep every base match; xG is null
    where Understat has no coverage.
  - *Strict Intersection* — keep only matches both sources cover.
  - Optionally, fit Dixon-Coles directly on xG instead of raw goals
    (`DixonColesModel.fit(goal_columns=("home_xg", "away_xg"))`) — a
    lower-variance target, at the cost of xG not being literally
    Poisson count data (see that method's docstring).
- **The Odds API** (`src/ingestion/odds_feed.py:fetch_live_odds`) — a
  *fixture-side* source, not historical: its free tier serves live
  upcoming odds only. `get_upcoming_fixtures` calls it automatically for
  every Big 5 league and averages the 1X2 price across every bookmaker
  in the response for a genuine multi-book consensus (not just whichever
  book comes first). Needs an API key — the sidebar only asks for one if
  `ODDS_API_KEY` isn't already set in the environment or
  `.streamlit/secrets.toml`.
- **football-data.co.uk free fixture sheet**
  (`src/ingestion/odds_feed.py:fetch_free_schedule`) — a second
  fixture-side fallback, no key needed: `fixtures.csv` on the same site
  as the historical results, refreshed roughly weekly (Fridays) with
  Bet365 pre-match odds for the coming weekend's Big 5 matches. A row
  missing any of the three 1X2 prices is dropped rather than filled
  with a placeholder — a fabricated price would make the EV computed
  against it meaningless on a tool whose job is finding real
  mispricings, not just approximately wrong in a way that looks fine.

**Not implemented** (real extension points, not fake stubs): **Betfair
Exchange** — its API-NG requires certificate-based login and a
registered application key this project has no credentials for; and
**FBref** — another xG source, left out to avoid doubling scraper-
maintenance surface for limited incremental benefit over Understat
alone. Both scrapers (Understat included) depend on site markup that
can change without notice — failures degrade to an empty result with a
logged warning rather than breaking the pipeline, but nothing here was
verified against a live pull (this sandbox has no outbound access).

## Project layout

```
config/          Hyperparameters and canonical team-name mappings
data/            Cached historical results, fixture cards, prediction ledger
src/ingestion/   Historical/fixture/odds ingestion, team normalization, offline demo data,
                 Understat xG scraper, multi-source blending
src/models/      Dixon-Coles fitting engine and 10x10 scoreline simulator
src/analytics/   De-vigging (multiplicative / Shin) and EV / Kelly sizing
src/validation/  Strict walk-forward backtest and evaluation metrics
src/tracking/    Append-only prediction ledger
src/pipeline.py  End-to-end CLI entry point + shared logic for the UI
app.py           Streamlit dashboard — Matchday (home) page
pages/           Streamlit dashboard — Model Diagnostics, Backtest, Ledger pages
```

## Deployment

Streamlit needs a persistent Python process with an open WebSocket
connection, so it cannot run on static-site or Vercel-style serverless
hosting — those platforms deploy prebuilt HTML/JS and have no long-lived
server to attach to. Use a host built for long-running processes instead:

**Streamlit Community Cloud (recommended — free, zero infra)**
1. Push this repo to GitHub (public, or private on a paid plan).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in, and
   click "New app".
3. Point it at this repo, branch, and `app.py` as the entry file.
4. Deploy. `requirements.txt` and `runtime.txt` are picked up automatically.

**Render / Railway (Procfile-based)**
- Both auto-detect the included `Procfile`
  (`web: streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`).
  Connect the repo, and set the start command to the Procfile's `web` line
  if it isn't picked up automatically.

**Fly.io / any Docker host**
- The included `Dockerfile` builds a self-contained image exposing port
  8501: `docker build -t dvpe . && docker run -p 8501:8501 dvpe`.

`.streamlit/config.toml` sets headless mode so none of these need a
browser to complete setup.

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

Also implemented: multi-source historical blending (football-data.co.uk +
Understat xG) and a live odds-provider integration (The Odds API,
multi-bookmaker consensus) — see "Multi-source data blending" above.

Not yet wired: the `xi` decay grid search itself, which is exposed as a
config surface (`model.xi_grid` in `config/settings.yaml`) for the
validation harness to sweep; and Betfair Exchange / FBref as additional
sources (see caveats above).

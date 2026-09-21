# Draw Value Prediction Engine (DVPE) — Football Big 5

Implementation planning: the [prioritized task index](tasks/todo.md) links all **58 detailed tasks** with [scope, dependencies, acceptance criteria, and quality gates](tasks/detailed-backlog.md). All tasks remain open; no implementation or release approval is implied.

See the [complete review index](docs/README.md) for all engine, UI/UX, and [improvement/enhancement findings](docs/improvements-2026-09-20.md), ordered by priority and linked to the [shared backlog](tasks/todo.md). Recommendations are documented only, not implemented.

> **Audit update — 2026-09-20:** The current implementation contains production-reachable synthetic/static data and fallback paths, mathematical defects, and incomplete evidence gates. Earlier completion/readiness statements below describe development history, not verified production readiness. See the [prioritized audit](docs/audit-2026-09-20.md) and [proposed frontend/backend migration](docs/frontend-backend-migration.md). This audit changed documentation only; findings remain open.

Audit documentation: [engine/data findings — A01–A20](docs/audit-2026-09-20.md), [UI/UX findings — UX01–UX12](docs/ui-ux-audit-2026-09-20.md), [architecture migration](docs/frontend-backend-migration.md), and [prioritized backlog](tasks/todo.md). Findings remain open; documenting them does not mark them fixed.

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

- **Matchday** (`app.py`) — a Score Predictor view: every analyzed
  fixture gets a card with its most-likely final score, runner-up
  score, xG, and a home/draw/away probability bar, all extracted from
  the real fitted 10x10 scoreline matrix (`src/models/simulator.py:
  top_scorelines`) — not a random or simulated number. A "HIGH/MODERATE
  TIE POTENTIAL" badge flags draw-likely fixtures, alongside a neutral
  "FORECAST ONLY" badge — stake/payout language and the EV qualification
  badge are deliberately kept off this primary card view (see
  "Compliance & durable ledger" below); the underlying +3% EV
  qualification logic still runs and is recorded to both ledgers, it's
  just not surfaced here. Pick historical
  data sources (offline demo generator, or football-data.co.uk +
  Understat to blend, or a CSV upload) in the sidebar; upcoming fixtures
  are fetched automatically through a three-tier chain — live consensus
  odds from The Odds API if a key is set, then the free weekly
  football-data.co.uk fixture sheet (no key needed), then a bundled
  sample fixture card as a last resort.
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

## Season-aware odds selection & strict validation (WP1)

football-data.co.uk's odds columns aren't consistent across its
archive: the market-consensus column was renamed from `BbAvH/D/A`
(Betbrain) to `AvgH/D/A` partway through, Pinnacle columns only appear
in later seasons, and older seasons may carry only Bet365. The
previous loader hardcoded a single `AvgH/D/A` → `B365H/D/A` fallback,
which silently starved the model of prices for any season that had
neither.

`src/ingestion/data_loader.py` replaces this with a season-aware
hierarchy (`select_match_odds`, used inside `normalize_dataframe`):
market average → legacy Betbrain average → Pinnacle closing → Pinnacle
→ Bet365 → market maximum → legacy Betbrain maximum, in that trust
order (a multi-book average is the least single-book-biased signal;
market *maximum* is kept only as a last resort since it systematically
overstates the fair price). Every row picks whichever tier it actually
has data for; the tier used is recorded in a new `price_source` column
for auditability, mirroring the `price_source` field already tracked
per-prediction in the Supabase ledger.

A separate `validate_matches` step runs after normalization (wired
into `src.pipeline.load_historical_matches`, `src.ingestion.sources
.load_and_blend_sources`, and the Matchday CSV-upload path) and drops
rows that would corrupt the Dixon-Coles fit — missing/invalid date,
team, or goal data, a fixture with identical home/away teams, or an
exact duplicate. An implausible single-row odds triple (≤1.01, not a
valid decimal price) is nulled out rather than dropping the match
itself — a match without a usable price is still valid goal-model
training data; `src/validation/backtest.py` already skips NaN-odds
rows when scoring market comparisons. Every caller logs (or, for the
CSV upload, surfaces in the UI) a report of how many rows were kept vs.
dropped and why.

## Dixon-Coles model corrections (WP2)

`src/models/dixon_coles.py` — four corrections to the fitting core,
kept scipy-only (no JAX/autodiff dependency, by explicit choice):

- **N-1 reparameterization**: one well-observed reference team is
  pinned at (alpha=0, beta=0) during optimization for identifiability
  (standard reference-category coding), then every team's published
  rating is re-centered onto the sum-to-zero basis via a mean shift
  absorbed into `mu0_` — a pure change of basis that leaves every
  fitted lambda/mu, and therefore the likelihood, exactly unchanged.
- **Per-season gamma**: home advantage is fit as one value per season
  present in the training window (`gamma_by_season_`) instead of a
  single constant across the whole rolling window — it measurably
  drifts season to season (e.g. behind-closed-doors matches). `predict`
  uses the most recently fitted season's value (`gamma_`) as the best
  available estimate for a fixture in a season not yet played.
- **Smooth shrinkage**: a team's rating is penalized toward the league
  mean by a continuous `min_matches / n_matches` ridge weight instead
  of the old hard cutoff that fully zeroed out anything below
  `min_matches` and left anything above it untouched — no more
  discontinuity between a 14-match and a 16-match team.
  `regularized_teams_` is kept as a diagnostic list of sparse teams,
  but they now get a real (heavily shrunk, not hardcoded-zero) rating.
- **Profiled rho**: rho is optimized in an outer 1-D bounded search
  over the *profile* negative log-likelihood (mu0/gamma/alpha/beta
  re-optimized at each candidate rho) instead of jointly with every
  other parameter in one high-dimensional optimization. rho is a weak,
  narrow-support nuisance parameter (it only touches four low-score
  cells via the tau adjustment) that destabilizes a joint fit —
  profiling it out is the standard fix, and what Dixon & Coles (1997)
  do. This multiplies fit cost by roughly the number of profile
  evaluations (capped at 15, with a coarse `1e-3` tolerance — rho
  doesn't reward more precision than that); `src/validation/backtest.py`
  refits periodically through a walk-forward run, so a full backtest is
  correspondingly slower than before.

## Gate evaluation harness (WP3)

`src/validation/{bootstrap,clv,calibration,three_way,gate_harness,
gate_report}.py` — a rigorous, per-league walk-forward evidence-pack
harness for the go/no-go gate decision, separate from
`src/validation/backtest.py` (the existing Streamlit Backtest page's
simpler single-price/single-xi harness, left untouched so that page
keeps working).

- **`bootstrap.py`**: seeded percentile bootstrap CIs that resample row
  *indices* rather than values — works for 1-D or 2-D data, or a
  DataFrame, unlike `np.random.choice` on raw values (which only
  accepts 1-D input and crashes on anything ROI-shaped once a league
  has more than a handful of bets). Includes a block bootstrap
  (resamples whole matchweeks, since bets in the same round share
  information and closing-line timing) and a paired bootstrap (for a
  Diebold-Mariano-style CI on a per-match difference statistic, e.g. a
  log-loss edge).
- **`clv.py`**: two-price CLV (`entry_odds/close_odds - 1`) evaluated
  both as a baseline (every fixture with both prices, regardless of
  whether the model flagged it) and on flagged bets only — the model
  only demonstrates value if flagged CLV clears the baseline, not
  merely zero. Samples of 10 or fewer report `NaN` plus an
  `insufficient_sample` flag rather than a 0.00% that reads as a real
  result.
- **`calibration.py`**: Cox calibration-regression slope/intercept
  (reliability-by-decile reuses the existing `metrics.calibration_curve`
  at `n_bins=10` — no need to duplicate it).
- **`three_way.py`**: full 3-outcome log-loss and RPS (Ranked
  Probability Score — rewards being *closer* on the ordinal Home <
  Draw < Away scale when wrong, unlike log-loss/Brier), complementing
  the existing draw-only `log_loss`/`brier_score` in `metrics.py`.
- **`gate_harness.py`**: the orchestrator. Per-league ξ (not one
  constant); team-ID-keyed warm starts across retrains, so a season
  boundary with promoted/relegated teams doesn't silently misalign
  parameters (a new team just gets the ordinary (0, 0) prior —
  `DixonColesModel.fit`'s new `warm_start` argument); exclusion
  tracking with reason codes (`insufficient_league_history`,
  `unseen_team`) carrying date/season/league/fixture_id; fits cached to
  disk keyed by (league, retrain date, ξ, git commit hash) so re-running
  the report doesn't refit identical work; asserts `date` is already
  datetime64 (UTC) rather than parsing it — that's the ingestion
  layer's job (WP1), not the harness's. A two-price entry/close pair
  comes from `src.ingestion.data_loader.select_entry_close_odds`:
  Pinnacle's own opening line vs. its own closing line (the standard
  proxy when true bet-placement timestamps aren't available — comparing
  one sharp book against itself avoids cross-book bias), falling back
  to Bet365 for either leg on older seasons; Bet365's draw price is
  also captured separately (`retail_draw`) for evaluating CLV against a
  realistic recreational-bettor price.
- **`gate_report.py`**: `compute_gate_decision` turns per-league CLV
  bounds into a decision — proceed with leagues clearing a positive
  lower CLV bound, reposition as a forecasting tool if every league's
  upper bound is at or below zero, otherwise inconclusive — computed on
  numeric bounds (never a pre-formatted string), so it stays
  machine-actionable; `render_gate_report_markdown` formats the result.

**Known gaps, not yet built**: COVID-season (2019/20 run-in, 2020/21)
flagging in the report — home-advantage estimates spanning those
matches are overstated and the by-season breakdown (`results["season"]`
is captured per row, so `groupby(["league","season"])` on the harness's
own output already supports this breakdown — just no dedicated helper
or explicit COVID callout yet) would show it, but nothing surfaces it
automatically today. Cross-league parallelization — `run_gate_evaluation`
loops leagues sequentially; disk caching means a *repeated* report
doesn't refit, but a first run over the full corpus is not parallelized
(deliberately deferred: multiprocessing a `DixonColesModel`/scipy
optimizer safely on Streamlit Cloud's typically memory-constrained free
tier is real added complexity for a one-time cost the cache already
amortizes on every subsequent run). A named "log-loss edge" convenience
wrapping `bootstrap.paired_bootstrap_ci` — the primitive is built and
tested, but no default report field calls it yet.

**Not implemented** (real extension points, not fake stubs): **Betfair
Exchange** — its API-NG requires certificate-based login and a
registered application key this project has no credentials for; and
**FBref** — another xG source, left out to avoid doubling scraper-
maintenance surface for limited incremental benefit over Understat
alone. Both scrapers (Understat included) depend on site markup that
can change without notice — failures degrade to an empty result with a
logged warning rather than breaking the pipeline, but nothing here was
verified against a live pull (this sandbox has no outbound access).

## Compliance & durable ledger

**UI compliance.** Every page shows a "NOT FINANCIAL ADVICE" banner with
a Responsible Gambling link (`RG_URL` env var / secret, defaults to
BeGambleAware) — the sidebar carries the same notice. The Matchday
cards intentionally omit stake/payout figures and the "+EV PLAY" badge;
that computation still runs in `build_predictions` and is written to
both ledgers below, it's just not displayed on the primary card.

**Local ledger** (`src/tracking/ledger.py`) is unchanged — an
append-only Parquet/CSV file under `data/`, read by the Ledger page.
It's ephemeral on hosts without a persistent disk (e.g. a fresh
Streamlit Cloud container), which is why the Supabase ledger below
exists as a durable, additive companion, not a replacement.

**Supabase remote ledger** (`src/tracking/supabase_ledger.py`) writes
every prediction to a managed Postgres table outside the app's own
container, gated by Row Level Security to INSERT + SELECT only on the
anon/publishable key — there is no UPDATE or DELETE policy, so a
prediction can't be edited after the fact. Settling a fixture is a
separate append-only insert into `settlements`, never a mutation of its
`predictions` row. To enable it:

1. Create a Supabase project, then run `supabase/schema.sql` once in
   its SQL Editor (Project → SQL Editor → New query) — this module only
   holds the anon key, which has no DDL access to create the tables
   itself.
2. Set `SUPABASE_URL` and `SUPABASE_ANON_KEY` (the `sb_publishable_...`
   key from Project Settings → API) via environment variables or
   `.streamlit/secrets.toml` (gitignored — never commit this file).

Requires `supabase==2.31.0`, pinned in `requirements.txt`: the older
`2.3.0` release validates API keys locally against a hardcoded
JWT-shaped regex and rejects the newer `sb_publishable_`/`sb_secret_`
key format before ever making a network call.

If Supabase isn't configured, `get_supabase_client()` returns `None`
and the app runs exactly as before — the write is skipped, not
retried or errored. A sidebar "System Health" panel reports both
ledgers' write status and failure counts after each run.

## Project layout

```
config/          Hyperparameters and canonical team-name mappings
data/            Cached historical results, fixture cards, prediction ledger
src/ingestion/   Historical/fixture/odds ingestion, team normalization, offline demo data,
                 Understat xG scraper, multi-source blending, season-aware odds hierarchy
                 + strict validation (data_loader.py)
src/models/      Dixon-Coles fitting engine and 10x10 scoreline simulator
src/analytics/   De-vigging (multiplicative / Shin) and EV / Kelly sizing
src/validation/  Strict walk-forward backtest and evaluation metrics, plus the WP3
                 gate-evaluation harness (bootstrap, CLV, calibration, three-way scoring,
                 gate_harness.py, gate_report.py)
src/tracking/    Append-only local prediction ledger + additive Supabase remote ledger
supabase/        schema.sql — run once in the Supabase SQL editor to enable the remote ledger
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
Understat xG), a live odds-provider integration (The Odds API,
multi-bookmaker consensus — see "Multi-source data blending" above),
PID Phase 0 (compliance UI + additive Supabase durable ledger — see
"Compliance & durable ledger" above), WP1 (season-aware odds hierarchy
+ strict validation), WP2 (Dixon-Coles model corrections), and WP3
(gate-evaluation harness — see those sections above). Per the WP1/WP2
evidence-pack gate, WP3 has not yet been run against the full
historical corpus.

Not yet wired: the `xi` decay grid search itself, which is exposed as a
config surface (`model.xi_grid` in `config/settings.yaml`) for the
validation harness to sweep; COVID-season flagging and cross-league
parallelization in the WP3 harness (see that section's "Known gaps"
above); and Betfair Exchange / FBref as additional
sources (see caveats above).

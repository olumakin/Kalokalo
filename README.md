# Draw Value Prediction Engine (DVPE) — Football Big 5

Automated quantitative pipeline that identifies mispriced draw outcomes
across the top five European football leagues (EPL, La Liga, Serie A,
Bundesliga, Ligue 1). Compares a time-decayed Dixon-Coles bivariate
Poisson goal model against de-vigged market consensus odds and sizes
positions with Fractional Kelly under dual exposure caps.

See the Project Initiation Document for full modeling detail, risk
framework, and phased delivery plan. See `docs/architecture.md` for the
public/admin trust-boundary diagram (Phase 0 revised).

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

A public, five-page Streamlit app. Two pages are open to everyone;
three are admin-only (Streamlit OIDC login — see "Public deployment &
admin auth" below):

- **Matchday** (`app.py`, public) — a read-only Score Predictor view.
  Every published fixture gets a card with its most-likely final score,
  runner-up score, xG, and a home/draw/away probability bar, all
  derived at read time from the stored λ/μ/ρ via the real fitted 10x10
  scoreline matrix (`src/models/simulator.py: build_score_matrix,
  top_scorelines`) — not a random or simulated number, and not
  recomputed differently than what an admin's run actually fit. A
  "HIGH/MODERATE TIE POTENTIAL" badge flags draw-likely fixtures,
  alongside a neutral "FORECAST ONLY" badge. **No stake, return, or
  profit figure is ever shown to a public visitor** — this page's only
  data source is `fetch_latest_predictions()` (a Supabase read); it
  never fits a model or calls a data feed for anyone who isn't a
  signed-in admin. A signed-in admin additionally sees the sidebar's
  data-source/Run-pipeline controls, and (only for a league whose
  `gate_status` is `'proceed'` with `approved_at` set — see below) a
  "Shadow: not validated" expander with the computed EV/stake.
- **Model Diagnostics** (`pages/1_Model_Diagnostics.py`, public) —
  fitted hyperparameters (μ₀, γ, ρ), convergence/fallback status, and
  per-team attack/defense ratings with charts, from whichever model is
  in the current browser session's state (only populated after an
  admin's own pipeline run in that session — a fresh visitor sees "run
  the pipeline first" and nothing else, the same graceful empty state
  this page already had).
- **Backtest** (`pages/2_Backtest.py`, admin-only) — runs the strict
  walk-forward engine over the admin's last-loaded history and reports
  log-loss, Brier score, flat-stake ROI, max drawdown, and a
  calibration curve.
- **Ledger** (`pages/3_Ledger.py`, admin-only) — full prediction audit
  trail from Supabase with filters, and a form to record a settlement
  (`settlements` table — never modifies the original `predictions` row).
- **Terms of Use** (`pages/4_Terms.py`, public) — placeholder legal
  text, clearly marked pending review.

Admin's only historical data source is football-data.co.uk (no demo
mode, no CSV upload, no Understat/xG blending — see "Removed from the
live app" below); upcoming fixtures are fetched automatically through a
three-tier chain — live consensus odds from The Odds API if a key is
set, then the free weekly football-data.co.uk fixture sheet (no key
needed), then a bundled sample fixture card as a last resort.

## Removed from the live app (Phase 0 revised)

The app going public removed several things from the live admin path
that still exist in the repo as tested, reusable modules — not deleted,
just no longer reachable from `app.py`:

- **CSV upload and offline demo data** — the app's only public purpose
  now is showing real published predictions, so a way to fit the model
  on arbitrary uploaded data or synthetic data no longer belongs in the
  live path at all, for any user. `src/ingestion/demo_data.py` moved to
  `tests/fixtures/demo_data.py` — a test fixture only; no app code
  imports it (enforced by `tests/test_public_view_leakage.py`).
- **Understat xG blending / "Fit on xG"** (WP8) — `src/ingestion
  /sources.py` and `src/ingestion/understat_xg.py` still exist, are
  still tested (`tests/test_sources.py`, `tests/test_understat_xg.py`),
  and are marked with a `# WP8` comment at the top of each — re-evaluate
  wiring them back in as a future work package, not lost work.
  football-data.co.uk alone gives goals and closing odds but no
  shot-quality signal, which is exactly the gap Understat's match-level
  xG (scraped from an embedded JSON blob on Understat's league pages —
  no public API exists) was meant to close; see those two modules'
  docstrings for the scraping/blending mechanics if reviving this.
- **The Odds API / free-schedule fixture feed** — unchanged, still
  live: `src/ingestion/odds_feed.py:get_upcoming_fixtures` averages the
  1X2 price across every bookmaker in the response for a genuine
  multi-book consensus. Needs an API key for the live tier — the admin
  sidebar only asks for one if `ODDS_API_KEY` isn't already set in the
  environment or `.streamlit/secrets.toml`; a row missing any of the
  three 1X2 prices is dropped by the free-schedule tier rather than
  filled with a placeholder.

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

## Public deployment & admin auth (Phase 0 revised)

The app is public. **Supabase is the only ledger** — the local file
ledger (`src/tracking/ledger.py`) has been deleted; see "One-time local
ledger migration" below if this deployment ever ran with it active.

**Age gate & legal notices.** A public visitor must confirm 18+ before
any content renders (`st.session_state["age_confirmed"]`, session-scoped
— confirmed once per browser session, not stored server-side). Every
page carries a "NOT FINANCIAL ADVICE" banner with a Responsible
Gambling link (`RG_URL` env var / secret, defaults to BeGambleAware);
`pages/4_Terms.py` is a placeholder Terms of Use page pending legal
review.

**No stakes for the public.** The Matchday cards show forecasts and
probabilities only — fixture, kickoff, P(home)/P(draw)/P(away),
most-likely scorelines, feed tier, and how long ago the prediction was
priced. No stake, return, profit, or bankroll figure is ever shown to a
visitor who isn't a signed-in admin; enforced by
`tests/test_public_view_leakage.py`, which asserts every reference to
`stake_shadow`/`ev_entry` in `app.py` falls inside an `if admin:` block
(via AST, not a string grep, so it can't be fooled by comments).

**Admin authentication.** Uses Streamlit's built-in OIDC support
(`st.login`/`st.user`/`st.logout`, `src/webapp/auth.py`) plus an email
allow-list — `is_admin()` requires both a signed-in user *and* an email
on that list, wrapped defensively so an unconfigured `[auth]` block
degrades to "not admin" rather than crashing the public page. To enable
it, register an OIDC app with a provider (Google Cloud Console is the
path of least resistance) and add to `.streamlit/secrets.toml`
(gitignored — never commit this file):

```toml
[auth]
redirect_uri = "https://<your-app>.streamlit.app/oauth2callback"
cookie_secret = "<random string>"
client_id = "..."
client_secret = "..."
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[admin]
emails = ["you@example.com"]
```

Until `[auth]`/`[admin]` are configured, every visitor (including you)
sees the public, read-only view only — there's no way to run the
pipeline or reach the admin pages except through a working OIDC login.
`2_Backtest.py` and `3_Ledger.py` both call `require_admin()` as their
very first line: a non-admin sees exactly the word "Restricted" and
nothing else on either page (verified via `AppTest` in
`tests/test_app_public_access.py`).

**Staking gate.** A `gate_status` table (one row per league,
`league_code, status, run_id, decided_at, approved_by, approved_at`)
controls whether an admin ever sees a computed stake at all — seeded to
`status='inconclusive'` for all five leagues by `supabase/schema.sql`.
There is no in-app UI to change it: approving a league is a deliberate,
manual Supabase-side action (set `status='proceed'` and `approved_at`),
not a feature of this app. Every pipeline run still computes and writes
`stake_shadow`/`bankroll_at_slate` to `predictions` regardless of gate
status (so the record exists once a league is later approved) — gate
status only controls whether the admin UI *displays* it, labelled
"Shadow: not validated".

**Supabase schema.** `src/tracking/supabase_ledger.py` writes every
prediction to a managed Postgres table, gated by Row Level Security to
INSERT + SELECT only on the anon/publishable key for
`predictions`/`settlements` (no UPDATE or DELETE policy on either —
a prediction can't be edited after the fact, and settling a fixture is
a separate append-only insert into `settlements`, never a mutation of
its `predictions` row) and SELECT only on `gate_status` (the app never
writes it). To set up:

1. **Fresh Supabase project**: run `supabase/schema.sql` once in the
   SQL Editor (Project → SQL Editor → New query).
   **Existing project** (already ran the original Phase 0 schema):
   run `supabase/migrations/0002_phase0_revised.sql` instead — it
   `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`s the new fields onto your
   existing tables and adds `gate_status`, rather than trying to
   `CREATE TABLE` something that already exists.
2. Set `SUPABASE_URL` and `SUPABASE_ANON_KEY` (the `sb_publishable_...`
   key from Project Settings → API) via environment variables or
   `.streamlit/secrets.toml`.

Requires `supabase==2.31.0`, pinned in `requirements.txt`: the older
`2.3.0` release validates API keys locally against a hardcoded
JWT-shaped regex and rejects the newer `sb_publishable_`/`sb_secret_`
key format before ever making a network call.

If Supabase isn't configured or unreachable, `get_supabase_client()`
returns `None` and every write function degrades to "failed, logged"
rather than raising — but unlike Phase 0 (where this was an additive
side-channel), Supabase is now the *only* ledger, so the admin sidebar
and Ledger page both show a "🔴 Ledger offline" status
(`is_ledger_online()`, a cheap 1-row probe) and grey out every
write-triggering control (Run pipeline, Record outcome) until it
recovers.

**One-time local ledger migration.** If this deployment ever ran with
the (now-deleted) local file ledger active, its rows need moving across
once:

```bash
python -m scripts.migrate_local_ledger_to_supabase --path data/ledger.parquet
```

Reads the old parquet/csv directly (doesn't import the deleted
`Ledger` class), maps each row onto the new schema — fields the old
ledger never captured (λ/μ/ρ, matchweek, three-way entry/close prices,
...) are written `NULL`, the honest representation, not a guess — and
also writes a `settlements` row for any old entry that already had a
result recorded (reverse-deriving the closing draw price from the old
ledger's `clv` field where possible). Prints a report of rows
read/written/failed/skipped; safe to re-run (idempotent upsert on the
predictions side).

## Project layout

```
config/          Hyperparameters and canonical team-name mappings
data/            Cached historical results, fixture cards
src/ingestion/   Historical/fixture/odds ingestion, team normalization,
                 season-aware odds hierarchy + strict validation (data_loader.py);
                 sources.py / understat_xg.py still exist, tested, but unwired (WP8)
src/models/      Dixon-Coles fitting engine and 10x10 scoreline simulator
src/analytics/   De-vigging (multiplicative / Shin) and EV / Kelly sizing
src/validation/  Strict walk-forward backtest and evaluation metrics, plus the WP3
                 gate-evaluation harness (bootstrap, CLV, calibration, three-way scoring,
                 gate_harness.py, gate_report.py)
src/tracking/    Supabase-backed prediction ledger (the only ledger — the local file
                 ledger was retired in Phase 0 revised)
src/webapp/      Admin authentication (auth.py: is_admin/require_admin, Streamlit OIDC)
supabase/        schema.sql (fresh project) / migrations/0002_phase0_revised.sql
                 (existing project) — run once in the Supabase SQL editor
scripts/         migrate_local_ledger_to_supabase.py — one-time local-ledger migration
src/pipeline.py  End-to-end CLI entry point + shared logic for the UI
app.py           Streamlit dashboard — Matchday (public home) page
pages/           Model Diagnostics (public), Backtest (admin), Ledger (admin),
                 Terms of Use (public)
tests/fixtures/  Offline synthetic match-history generator — test-only, not imported
                 by any app code (demo_data.py)
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

Also implemented: a live odds-provider integration (The Odds API,
multi-bookmaker consensus), WP1 (season-aware odds hierarchy + strict
validation), WP2 (Dixon-Coles model corrections), WP3 (gate-evaluation
harness), and **Phase 0 revised** — the app is public: admin-gated
Streamlit OIDC auth, Supabase as the sole ledger (local file ledger
retired), no stakes shown to public visitors, a `gate_status` table
controlling staking eligibility per league (seeded to `inconclusive`
for all five), age gate + Terms of Use + Responsible Gambling notices,
and CSV upload / offline demo data / Understat xG removed from the
live app entirely (see "Removed from the live app" and "Public
deployment & admin auth" above). Per the WP1/WP2 evidence-pack gate,
WP3 has not yet been run against the full historical corpus, and no
league's `gate_status` has been set to `proceed` — every shadow stake
is computed and stored but hidden from the admin UI.

Not yet wired: the `xi` decay grid search itself, which is exposed as a
config surface (`model.xi_grid` in `config/settings.yaml`) for the
validation harness to sweep; COVID-season flagging and cross-league
parallelization in the WP3 harness (see that section's "Known gaps"
above); Betfair Exchange / FBref as additional sources; and Understat
xG / multi-source blending, deferred to WP8 (see "Removed from the
live app" above).

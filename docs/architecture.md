# System Architecture — Draw Value Prediction Engine (DVPE)

For the presentation layer, see the [UI/UX source audit](ui-ux-audit-2026-09-20.md), including 12 prioritized findings and user-journey acceptance requirements. All findings remain open.

> **2026-09-20 review:** This earlier description contains implementation discrepancies (including defence sign, rho bounds, Kelly terminology, rolling-window enforcement, and ledger behavior). The [dated source audit](audit-2026-09-20.md) records current behavior and supersedes conflicting claims. The [frontend/backend proposal](frontend-backend-migration.md) describes the recommended future architecture, not implemented changes.

Automated quantitative pipeline identifying mispriced draw outcomes across Europe's top five football leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1).

```
+---------------------------------------------------------------------------------------+
|                                    DATA INGESTION                                     |
|  football-data.co.uk (Archive) | Understat (xG Scrape) | The Odds API (Live Consensus)|
|                   +------------------------+-------------------------+                |
|                                            v                                          |
|                               src.ingestion.data_loader                               |
|                     (Season-aware odds hierarchy + match validation)                  |
+--------------------------------------------+------------------------------------------+
                                             |
                                             v
+---------------------------------------------------------------------------------------+
|                                  MODELING & SIMULATION                                |
|                                src.models.dixon_coles                                 |
|          - Time-decayed Poisson likelihood (xi_decay = 0.0065, ~730d window)          |
|          - N-1 identifiability reparameterization (reference team alpha=0, beta=0)    |
|          - Profiled rho 1-D bounded optimization over profile NLL                     |
|          - Per-season home advantage drift (gamma_s)                                  |
|          - Smooth continuous ridge shrinkage for sparse / promoted teams              |
|                                            |                                          |
|                                            v                                          |
|                                 src.models.simulator                                  |
|                     - 10x10 bivariate Poisson scoreline grid                          |
|                     - Dixon-Coles tau adjustment on low scores (0,0), (1,0)...        |
|                     - 1X2 probabilities + top scorelines extraction                   |
+--------------------------------------------+------------------------------------------+
                                             |
                                             v
+---------------------------------------------------------------------------------------+
|                               ANALYTICS & POSITION SIZING                             |
|                                    src.analytics                                      |
|          - devig.py: Multiplicative & Shin de-vigging algorithms                      |
|          - edge.py: EV qualification (EV >= +3%) & Fractional Kelly sizing (0.15)     |
|          - Dual risk caps: 2.5% single-match cap, 8.0% daily portfolio slate cap      |
+--------------------------------------------+------------------------------------------+
                                             |
                      +----------------------+----------------------+
                      v                                             v
+-------------------------------------------+ +-----------------------------------------+
|           VALIDATION & GATE HARNESS       | |           TRACKING & INTERFACES         |
|                 src.validation            | |                                         |
|  - backtest.py: Strict walk-forward       | |  - Streamlit Multipage Web App:         |
|  - bootstrap.py: Block (matchweek) &      | |      app.py (Matchday Score Predictor)  |
|    paired bootstrap CIs                   | |      pages/1_Model_Diagnostics.py       |
|  - clv.py: 2-price Closing Line Value     | |      pages/2_Backtest.py                |
|  - calibration.py: Cox regression         | |      pages/3_Ledger.py                  |
|  - three_way.py: Log-loss & RPS           | |  - src.tracking.ledger: Local Parquet   |
|  - gate_harness.py: Evidence pack engine  | |  - src.tracking.supabase_ledger:        |
|  - gate_report.py: Go/No-go gate decision | |      Remote append-only RLS Postgres    |
+-------------------------------------------+ +-----------------------------------------+
```

---

## 1. Directory Structure

```
Kalokalo/
|-- api/                       # REST API Service Layer
|   `-- main.py                # FastAPI application exposing domain engine endpoints
|-- app.py                     # Streamlit Matchday interface (presentation layer)
|-- pages/                     # Multipage Streamlit dashboards
|   |-- 1_Model_Diagnostics.py # Hyperparameters, attack/defense ratings
|   |-- 2_Backtest.py          # Walk-forward simulation & ROI curves
|   `-- 3_Ledger.py            # Local & remote audit ledger
|-- src/
|   |-- config.py              # Pydantic v2 typed configuration system (AppConfig)
|   |-- engine.py              # Headless DomainPredictionEngine
|   |-- pipeline.py            # Execution pipeline & league-level training/inference
|   |-- ingestion/             # Historical, odds feed, normalization, scrapers
|   |   |-- data_loader.py     # Odds hierarchy resolution & match validation
|   |   |-- demo_data.py       # Plausible offline data generator
|   |   |-- historical.py      # football-data.co.uk CSV downloader & cache
|   |   |-- normalizer.py      # Canonical team name resolution
|   |   |-- odds_feed.py       # Live consensus odds & free schedule fallback
|   |   |-- sources.py         # Multi-source blending coordinator
|   |   `-- understat_xg.py    # Understat scraper & xG alignment
|   |-- models/
|   |   |-- dixon_coles.py     # Bivariate Poisson engine with profiled rho (corrected tau)
|   |   `-- simulator.py       # Adaptive scoreline grid, tau admissibility & probability matrix
|   |-- analytics/
|   |   |-- devig.py           # Multiplicative and Shin de-vigging
|   |   `-- edge.py            # EV thresholding & Fractional Kelly sizing
|   |-- validation/
|   |   |-- backtest.py        # Strict chronological walk-forward engine
|   |   |-- bootstrap.py       # Percentile, block, and paired bootstrap
|   |   |-- calibration.py     # Cox calibration-regression & decile bins
|   |   |-- clv.py             # 2-price Closing Line Value analyzer
|   |   |-- gate_harness.py    # Multi-league evidence pack orchestrator
|   |   |-- gate_report.py     # Automated gate decision generator (strict baseline comparison)
|   |   |-- metrics.py         # Brier, log-loss, ROI, drawdown
|   |   `-- three_way.py       # 3-outcome log-loss and Ranked Probability Score
|   `-- tracking/
|       |-- ledger.py          # Append-only Parquet/CSV file ledger (atomic tmp writes)
|       `-- supabase_ledger.py # Append-only remote Postgres ledger with authenticated RLS
|-- config/
|   |-- settings.yaml          # Hyperparameters, window sizes, caps, leagues
|   `-- team_mappings.json     # Canonical alias dictionary across data sources
|-- data/
|   |-- fixtures/              # Upcoming match cards (upcoming.csv)
|   `-- historical/            # Cached raw league CSVs
|-- docs/                      # Architecture, invariants, and gate specs
|-- tasks/                     # Lessons learned and task backlog
|-- supabase/                  # schema.sql (DDL, check constraints & RLS policies)
`-- tests/                     # Comprehensive test suites (20 modules, 201 tests)
```

---

## 2. Core Subsystems

### Ingestion Subsystem (`src/ingestion/`)
- **Multi-tier fixtures**: Tries The Odds API (live consensus odds) -> football-data.co.uk free weekly schedule -> bundled `data/fixtures/upcoming.csv`.
- **Season-Aware Odds Hierarchy**: Multi-book average (`Avg`) -> Betbrain average (`BbAv`) -> Pinnacle closing (`PSCH`) -> Pinnacle opening (`PSH`) -> Bet365 (`B365`) -> Market maximum (`Max`).
- **Validation Gate**: Strips malformed dates, self-play fixtures, duplicates, and non-positive scores. Implausible odds ($\le 1.01$) are nulled out without dropping valid match scores.
- **xG Blending**: Merges Understat shot-quality metrics via *Blended Consensus* (retaining unmapped matches) or *Strict Intersection*.

### Modeling Subsystem (`src/models/`)
- **Dixon-Coles Objective**:
  $$\ln L(\theta) = \sum_{k=1}^N e^{-\xi (t - t_k)} \ln \left[ \tau(x_k, y_k; \rho, \lambda_k, \mu_k) \, \text{Pois}(x_k; \lambda_k) \, \text{Pois}(y_k; \mu_k) \right]$$
  where:
  $$\lambda_k = \exp(\mu_0 + \gamma_{s(k)} + \alpha_{h(k)} - \beta_{a(k)})$$
  $$\mu_k = \exp(\mu_0 + \alpha_{a(k)} - \beta_{h(k)})$$
- **N-1 Basis**: Pinned reference team $(\alpha_{\text{ref}} = 0, \beta_{\text{ref}} = 0)$ prevents collinearity drift, re-centered to $\sum \alpha = 0, \sum \beta = 0$ via mean shifts absorbed into $\mu_0$.
- **Profiled $\rho$**: Joint optimization over $\rho$ is ill-conditioned due to limited low-score support. Profile likelihood optimizes $(\mu_0, \gamma, \alpha, \beta)$ in an inner loop for candidate $\rho \in [-0.15, 0.10]$ via bounded 1-D Brent search.
- **Smooth Shrinkage**: Teams with fewer than `min_matches` (15) are penalized toward 0 via continuous ridge weight $\frac{N_{\min}}{N}$, eliminating cliff-edge promotion artifacts.

### Analytics Subsystem (`src/analytics/`)
- **De-vigging**: Removes the bookmaker margin (overround) to recover true fair market probabilities:
  - Multiplicative normalization: $p_i = \frac{1/o_i}{\sum 1/o_j}$
  - Shin method: Models bookmaker insider trading proportion $z$, resolving true probabilities non-linearly.
- **EV Qualification**: Flags bets where $\text{EV} = p_{\text{model}} \cdot o_{\text{market}} - 1 \ge 0.03$ (+3%).
- **Fractional Kelly**: Sizes stakes using quarter-Kelly ($f^* = 0.15$):
  $$f = \frac{p \cdot o - 1}{o - 1} \times 0.15$$
- **Dual Exposure Caps**: Hard upper bound of 2.5% bankroll per match; daily slate scaled proportionally if total exposure exceeds 8.0%.

### Validation Subsystem (`src/validation/`)
- **Strict Walk-Forward**: Refits the model at regular intervals, testing exclusively on strictly subsequent matches (zero future data leakage).
- **WP3 Gate Harness**: Comprehensive statistical evidence pack:
  - 2-price CLV against Pinnacle closing prices and Bet365 retail lines.
  - Matchweek block bootstrap preserving intra-round correlation.
  - Paired bootstrap for comparative edge metrics.
  - Multi-class Ranked Probability Score (RPS).
  - Automated gate decision: `PROCEED` (positive lower CLV CI), `REPOSITION_FORECASTING` (upper CI $\le 0$), or `INCONCLUSIVE`.

### Tracking & UI Subsystem (`src/tracking/` & `pages/`)
- **Local Parquet Ledger**: Append-only local storage under `data/ledger.parquet`.
- **Remote Supabase Ledger**: Append-only PostgreSQL writes via publishable anon key protected by Row Level Security (RLS) policies prohibiting UPDATE and DELETE.
- **Compliance UI**: Responsible Gambling disclaimers, forecast-only badges on primary cards, and non-financial advice notices across all views.

---

# Presentation & Web Application Layer (Streamlit OIDC & Supabase)

## Actors and trust boundary

```
                         ┌───────────────────────────┐
                         │   Public visitor (anon)   │
                         └─────────────┬─────────────┘
                                        │ HTTPS
                                        ▼
                         ┌───────────────────────────┐
                         │  Streamlit app (app.py +   │
                         │  pages/*.py)                │
                         │                             │
                         │  age gate → is_admin() ─────┼──▶ False for
                         │       │                      │   every anon
                         │       ▼                      │   visitor
                         │  PUBLIC VIEW:                │
                         │   fetch_latest_predictions() │
                         │   (Supabase SELECT only)     │
                         └─────────────┬───────────────┘
                                        │ anon key, RLS: SELECT-only
                                        ▼
                         ┌───────────────────────────┐
                         │        Supabase            │
                         │  predictions (RLS: I+S)    │
                         │  settlements (RLS: I+S)     │
                         │  gate_status (RLS: S only) │
                         │  no UPDATE/DELETE policy    │
                         │  on any table                │
                         └───────────────────────────┘
                                        ▲
                                        │ anon key, RLS: INSERT+SELECT
                         ┌─────────────┴───────────────┐
                         │  ADMIN VIEW (is_admin()==True)│
                         │   - st.login() / st.user       │
                         │     (Streamlit OIDC)           │
                         │   - email checked against      │
                         │     st.secrets["admin"]["emails"]│
                         │   - Run pipeline:               │
                         │       load_historical_matches   │
                         │       → fit_model                │
                         │       → get_upcoming_fixtures    │
                         │       → build_predictions        │
                         │       → record_predictions_      │
                         │         to_supabase (INSERT,     │
                         │         conflict-ignore)         │
                         │   - Shadow stakes shown only      │
                         │     where gate_status.status ==   │
                         │     'proceed' AND approved_at set │
                         │   - Backtest page (2_Backtest.py) │
                         │   - Ledger/settlement page        │
                         │     (3_Ledger.py) → write_        │
                         │     settlement (INSERT into       │
                         │     settlements, never updates    │
                         │     predictions)                  │
                         └───────────────┬────────────────┘
                                         │
                    ┌────────────────────┼─────────────────────┐
                    ▼                    ▼                     ▼
          football-data.co.uk     The Odds API          src/models
          (historical results,    (live odds, admin       Dixon-Coles fit,
           admin-triggered only)   run only)               10x10 simulator
```

## Key properties enforced by this structure

- **No unauthenticated write or compute path.** Every branch that fits a
  model, calls an external data feed, or writes to Supabase is inside
  `if admin:` (i.e. gated by `is_admin()`), verified by
  `tests/test_app_public_access.py` via `AppTest` (button/control
  absence) and by patching `load_historical_matches`/`fit_model` to raise
  if ever called from a logged-out session.
- **Public page never fits a model or calls a data feed.** Its only data
  path is `fetch_latest_predictions()`, a Supabase `SELECT` — confirmed by
  the same test file; no `requests.get` to football-data.co.uk or The Odds
  API exists outside the admin branch.
- **Supabase is the only ledger.** `src/tracking/ledger.py` (local
  Parquet/CSV file) is deleted; `src/pipeline.py`'s CLI path and the admin
  web path both write through `src/tracking/supabase_ledger.py`.
- **RLS is the actual enforcement point for "no update/delete."** The app
  never issues an UPDATE or DELETE against `predictions` or `settlements`
  in code, and the database additionally has no policy permitting either —
  a defense-in-depth pair, not just an app-level convention. `gate_status`
  is SELECT-only for the `anon` role; nothing in the app writes it (manual
  DB action by design).
- **Duplicate-run safety is a DB constraint, not app logic.**
  `predictions` has a unique key on `(fixture_id, model_version, run_id)`;
  writes use `upsert(..., ignore_duplicates=True)`.
- **Staking is gated per league, default "not eligible."** `gate_status`
  seeds all five leagues to `inconclusive`; a shadow stake is computed and
  stored on every admin run (so the pipeline itself never blocks), but is
  only *rendered* in the admin UI when `status == 'proceed' AND
  approved_at IS NOT NULL` — currently true for none of them.
- **xG/Understat is present in the repo but not reachable from any live
  code path** (`src/ingestion/understat_xg.py`, `sources.py`) — deferred
  to WP8, verified by grep-based tests
  (`tests/test_public_view_leakage.py`).

## What changed vs. the pre-Phase-0-revised diagram

| Old | New |
|---|---|
| Single ungated `app.py` script — anyone could trigger fit/fetch/write | Split into public view (Supabase read-only) and admin view (`is_admin()`-gated) |
| Local `src/tracking/ledger.py` (Parquet/CSV) + Supabase, written in parallel | Local ledger deleted; Supabase is the sole ledger for both the CLI and the web app |
| CSV upload / demo data selectable by any visitor | Removed entirely from the live app; demo data survives only as a test fixture (`tests/fixtures/demo_data.py`) |
| Understat xG / multi-source blend selectable in the sidebar | Unwired from `app.py`; modules retained, untested-by-UI, WP8-flagged |
| No staking gate — EV/stake shown to all visitors | `gate_status` table + admin-only "Shadow: not validated" display, default not eligible |
| No admin authentication | Streamlit OIDC (`st.login`/`st.user`) + email allow-list in `st.secrets["admin"]["emails"]` |
| No age gate / terms page | Age confirmation gate (session-scoped) + `pages/4_Terms.py` (draft) |

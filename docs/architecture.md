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

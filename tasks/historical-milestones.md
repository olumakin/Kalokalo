# Historical implementation milestones

Preserved from the pre-expansion backlog on 2026-09-20. Checked items record earlier development claims, not current audit closure or production acceptance. See [current tasks](todo.md) and [detailed task specifications](detailed-backlog.md). No audit task was completed by this documentation update.

### Phase 0: Compliance UI & Durable Supabase Ledger
- [x] Implemented Responsible Gambling banner with configurable `RG_URL`.
- [x] Added "FORECAST ONLY" badges and suppressed financial stake language on primary card views.
- [x] Authored `supabase/schema.sql` with strict append-only RLS policies (INSERT + SELECT only).
- [x] Built fail-closed `src/tracking/supabase_ledger.py` with automatic offline graceful degradation.

### Phase 1: MVP Core Pipeline & Streamlit UI
- [x] Time-decayed Dixon-Coles Poisson model with 10x10 scoreline simulator.
- [x] De-vigging algorithms (Multiplicative and Shin) and Fractional Kelly position sizing.
- [x] Dual risk exposure caps (2.5% single match, 8.0% daily slate).
- [x] Multipage Streamlit dashboard: Matchday, Model Diagnostics, Backtest, and Ledger.
- [x] Offline demo match history generator for sandboxed environments.

### WP1: Season-Aware Odds Hierarchy & Strict Validation
- [x] Implemented multi-tier odds fallback (`Avg` -> `BbAv` -> `Pinnacle Close` -> `Pinnacle Open` -> `Bet365` -> `Max`).
- [x] Added strict match validation (`validate_matches`) discarding corrupted dates, duplicate fixtures, and self-matches.
- [x] Odds source tracking (`price_source` audit column) across ingestion and ledger writes.

### WP2: Dixon-Coles Model Corrections (Scipy-Only)
- [x] N-1 reparameterization fixing reference team at $(\alpha=0, \beta=0)$, with post-fit zero-mean recentering.
- [x] Profiled $\rho$ outer 1-D bounded optimization over profile negative log-likelihood.
- [x] Per-season home advantage ($\gamma_s$) capturing historical drift.
- [x] Smooth continuous ridge shrinkage ($\frac{N_{\min}}{N}$) replacing discontinuous cutoffs.

### WP3: Gate Evaluation Harness
- [x] Seeded percentile bootstrap, matchweek block bootstrap, and paired bootstrap CIs.
- [x] 2-price Closing Line Value (CLV) evaluation vs sharp closing lines and recreational retail benchmarks.
- [x] Cox calibration-regression and decile reliability analysis.
- [x] Multi-outcome 3-way log-loss and Ranked Probability Score (RPS).
- [x] Disk caching for model fits keyed by `(league, retrain_date, xi, git_hash)`.
- [x] Machine-actionable gate decision engine (`PROCEED`, `REPOSITION_FORECASTING`, `INCONCLUSIVE`).

---

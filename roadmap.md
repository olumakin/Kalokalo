# 🗺️ Project Roadmap — Draw Value Prediction Engine (DVPE)

Task detail update: use the [58-task index](tasks/todo.md) and [detailed specifications and release gates](tasks/detailed-backlog.md) for execution planning. Migration stages are M01–M06; empirical validation is R01–R02; accessibility/user validation is QA01–QA02; future work is F01–F04. Earlier milestone claims remain historical, not evidence of passed gates.

Follow-up review: [IMP01–IMP12](docs/improvements-2026-09-20.md) adds secure packaging/logging, cache/configuration integrity, recovery, automation, monitoring, experiments, explanations, and measured optimization. Priorities and acceptance evidence are in the [combined backlog](tasks/todo.md); the [review index](docs/README.md) links every audit. None is implemented by this documentation update.

UI/UX work is now tracked in the [prioritized UI/UX audit](docs/ui-ux-audit-2026-09-20.md) and [task backlog](tasks/todo.md): UX01–UX06 address trust/task completion first; UX07–UX12 address consistency/comprehension next. Rendered accessibility/mobile verification remains outstanding. These supplement the engine audit and frontend migration; no findings are closed by documentation alone.

> **2026-09-20 audit supersedes readiness status below:** Development milestones are not production acceptance. Follow the [ranked open findings](docs/audit-2026-09-20.md) and [migration sequence](docs/frontend-backend-migration.md); correct mathematical/data-integrity blockers before a full-corpus approval run or frontend rollout. Documentation was updated; implementation fixes remain outstanding.

> Current Status: **WP1, WP2, WP3 & Phase 0 Complete** ✅
> Primary Stack: Python 3.11+, Scipy, Pandas, Streamlit, Supabase PostgreSQL, Pytest  
> Active Target: Full Historical Evidence Pack Run & Gate Decision Closure

---

## 📅 Roadmap Overview

```
[Phase 0: Compliance & Ledger] ──▶ [Phase 1: Core Engine] ──▶ [WP1: Odds Hierarchy]
               ▲                                                       │
               │                                                       ▼
[Phase 4: Production Gate] ◀── [WP3: Gate Harness] ◀── [WP2: Dixon-Coles Model]
```

---

## 🚀 Phases & Milestones

### ✅ Phase 0: Compliance & Durable Ledger (Completed)
- [x] Responsible Gambling UI notice with external helpline link (`RG_URL`).
- [x] Primary Matchday cards re-architected to neutral "FORECAST ONLY" / tie-potential view, suppressing stake language.
- [x] Remote durable Supabase PostgreSQL ledger (`src/tracking/supabase_ledger.py`) with append-only RLS policies.
- [x] Graceful fail-closed offline fallback for network-isolated deployments.

### ✅ Phase 1: Core Quantitative Engine & Dashboard (Completed)
- [x] Time-decayed Dixon-Coles bivariate Poisson modeling with 10x10 scoreline matrix.
- [x] De-vigging modules (Multiplicative and Shin models) in `src/analytics/devig.py`.
- [x] Fractional Kelly position sizing with dual exposure caps (2.5% single match, 8.0% daily portfolio).
- [x] Multipage Streamlit application: Matchday, Model Diagnostics, Backtest, and Ledger views.
- [x] Offline demo historical data generator for reproducible local development.

### ✅ WP1: Season-Aware Odds Hierarchy & Data Validation (Completed)
- [x] Dynamic fallback hierarchy across 20+ years of odds archive: `Avg` -> `BbAv` -> `Pinnacle Close` -> `Pinnacle Open` -> `Bet365` -> `Max`.
- [x] Strict match validation filter (`validate_matches`) discarding corrupted records, self-play, and invalid dates.
- [x] Implausible odds ($\le 1.01$) nulled to `NaN` preserving score data for model training.
- [x] Complete provenance auditing via `price_source` column tracking.

### ✅ WP2: Dixon-Coles Model Corrections (Completed)
- [x] **N-1 Reparameterization**: Reference team fixed at $(\alpha=0, \beta=0)$ during optimization, re-centered post-fit to zero mean.
- [x] **Profiled $\rho$ Optimization**: Outer 1-D Brent search over profile NLL eliminating joint optimization instability.
- [x] **Per-Season Home Advantage ($\gamma_s$)**: Season-specific home advantage coefficients capturing temporal drift.
- [x] **Smooth Shrinkage**: Continuous ridge regularization penalty ($\frac{N_{\min}}{N}$) replacing abrupt cutoffs.

### ✅ WP3: Gate Evaluation Harness (Completed)
- [x] Seeded percentile, block (matchweek), and paired bootstrap confidence interval engines.
- [x] Two-price Closing Line Value (CLV) evaluation vs sharp closing lines and recreational retail benchmarks.
- [x] Multiclass Rank Probability Score (RPS) and 3-way log-loss scoring.
- [x] Disk caching for fitted models keyed by `(league, retrain_date, xi, git_hash)`.
- [x] Machine-actionable gate decision rules (`PROCEED`, `REPOSITION_FORECASTING`, `INCONCLUSIVE`).

---

## 🔮 Phase 4: Production Validation & Full Corpus Gate (In Progress)

- [ ] **Full Historical Corpus Gate Run**:
  - Execute `src.validation.gate_harness` across full historical dataset (seasons back = 2-5) for all Big 5 leagues.
  - Generate comprehensive evidence report markdown artifact with confidence intervals.
- [ ] **Empirical $\xi$ Decay Sweep**:
  - Run grid search across $\xi \in [0.003, 0.010]$ to empirically select optimal half-life per league.
- [ ] **COVID-19 Season Adjustment**:
  - Introduce explicit indicator/weight adjustment for 2019/20 and 2020/21 crowdless fixtures.

---

## 🌟 Phase 5: Live Ingestion & Sentinel Operations (Future Backlog)

- [ ] **Automated Live Odds Polling**:
  - Scheduled background worker polling The Odds API / fixture sheets at regular intervals.
- [ ] **Alert Sentinel**:
  - Webhook integration (Telegram / Discord) triggering instant notification when $+EV$ draw opportunities qualify.
- [ ] **Exchange Liquidity & Betfair Integration**:
  - Investigate Betfair API-NG integration for real market depth and back/lay spread modeling.
- [ ] **Cross-League Worker Parallelization**:
  - Multi-threaded or process-pooled gate evaluation with memory limits suited for Streamlit Cloud.

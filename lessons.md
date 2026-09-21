# Lessons Learned — Draw Value Prediction Engine (DVPE)

> **2026-09-20 source review:** Earlier VERIFIED labels and convergence/fail-closed claims below are not supported as production guarantees. See the [audit and verification limits](docs/audit-2026-09-20.md), including corrected descriptions of rho bounds, ledger behavior and fixture caching. Preserve this history, but use reproducible evidence to close findings.

> Authoritative detailed ledger: [`tasks/lessons.md`](file:///c:/Users/akara/Documents/Projects/Kalokalo/tasks/lessons.md)

Forensic incident history, empirical modeling discoveries, architectural invariants, and anti-patterns encountered during development.

---

## 1. Core Model & Statistical Invariants

### Identifiability & Zero-Mean Basis
- Unconstrained Poisson models have infinite collinear solutions $(\alpha + c, \beta + c)$.
- Solution: Fix reference team $(\alpha_{\text{ref}} = 0, \beta_{\text{ref}} = 0)$ during L-BFGS-B optimization, then re-center post-fit ratings to zero mean ($\sum \alpha = 0, \sum \beta = 0$) with shifts absorbed into $\mu_0$. Goal intensities $\lambda, \mu$ remain mathematically identical.

### Profiled $\rho$ Bounded Search
- Joint optimization of Dixon-Coles $\rho$ with team ratings destabilizes because $\rho$ only impacts four low-scoring cells: $(0,0), (1,0), (0,1), (1,1)$.
- Solution: Profile $\rho$ out using outer 1-D Brent search over candidate $\rho \in [-0.15, 0.10]$, guaranteeing convergence in under 15 evaluations.

### Smooth Continuous Shrinkage
- Discontinuous hard match cutoffs ($<15$ matches zeroed out) caused cliff-edge rating distortions for newly promoted clubs.
- Solution: Continuous ridge penalty $\frac{N_{\min}}{N}$ penalizes sparse teams smoothly toward zero without step discontinuities.

---

## 2. Ingestion & Temporal Invariants

### Streamlit Cache TTL
- Live fixture functions cached with `@st.cache_data` without explicit `ttl` retain stale matchcards indefinitely across page refreshes.
- Mandatory Rule: All live fixture and odds functions MUST declare explicit TTLs (e.g. `ttl=3600` for schedule, `ttl=900` for odds).

### Competition Boundary Enforcement
- Multi-competition feeds can introduce cup fixtures or unmodeled leagues into upcoming cards.
- Mandatory Rule: Upcoming cards must strictly filter down to calibrated leagues (`E0`, `SP1`, `I1`, `D1`, `F1`) prior to normalization.

### Season-Aware Odds Hierarchy
- Column headers in football-data archives evolved across 20+ years (`BbAv` to `Avg`, Pinnacle introduction).
- Mandatory Rule: Dynamic multi-tier fallback: `Avg` -> `BbAv` -> `Pinnacle Close` -> `Pinnacle Open` -> `Bet365` -> `Max`.

---

## 3. Architecture & Compliance Invariants

### Append-Only Ledgers
- Prediction ledgers (local Parquet and remote Supabase PostgreSQL) must be append-only.
- Supabase Row Level Security (RLS) strictly permits `INSERT` and `SELECT` on anon key; `UPDATE` and `DELETE` are denied.
- Match settlement is an append-only entry in `settlements`, never an in-place edit of `predictions`.

### Non-Financial Advice Boundary
- The application is an analytical forecasting tool. UI surfaces responsible gambling disclaimers and neutral forecast badges, suppressing stake language on primary card views.

---

## 4. Audit Discoveries & Forensic Corrections (2026-09-20)

### Dixon-Coles Asymmetric Low-Score Factor Tau Inversion
- In Dixon & Coles (1997) Eq (2.1), $\tau(0,1) = 1 + \lambda \rho$ and $\tau(1,0) = 1 + \mu \rho$.
- Prior code had inverted them ($\tau(0,1) = 1 + \mu \rho$, $\tau(1,0) = 1 + \lambda \rho$), violating conservation of Poisson marginals under unequal home/away scoring rates.
- Mandatory Rule: $\tau(0,1)$ scales with home intensity $\lambda$; $\tau(1,0)$ scales with away intensity $\mu$. Negative grid cells raise `ValueError` instead of being silently clipped.

### No Silent Sample/Static Data Fallback Invariant
- Production prediction pipelines must never fall back to static demo data or sample CSV cards (`upcoming.csv`) when external feeds fail or API keys are missing.
- Mandatory Rule: `allow_sample=False` by default in all production and engine entry points. When data is unavailable, return typed error status (`FIXTURE_SOURCE_UNAVAILABLE`) with 0 recommendations.

### Cross-League Team Code Isolation
- Identical 3-letter abbreviations across different leagues (e.g. `MON` for Monza in Serie A and Monaco in Ligue 1) cause cross-league contamination if ratings are merged.
- Mandatory Rule: Team codes must be unique (`MNZ` for Monza, `MON` for Monaco) and models must be fit strictly per-league without cross-league rating bleed.

### Selection Quality vs. Baseline CLV in Gate Approval
- Statistical gate decision must not approve positive lower bound CLV if the flagged selections actually underperform the market baseline.
- Mandatory Rule: `flagged["mean_clv"] > baseline["mean_clv"]` is required alongside `ci_lo > 0` for `PROCEED`.

---

## 5. Verification & Status Ledger

```text
DIXON_COLES_OPTIMIZER =
  VERIFIED (Corrected asymmetric tau factors, L-BFGS-B with profiled rho, N-1 identifiability, smooth shrinkage)
STREAMLIT_CACHE_TTL =
  VERIFIED (Fixed ttl=3600 on fixtures, no stale memory leaks)
LEAGUE_BOUNDARY_FILTER =
  VERIFIED (Strict E0, SP1, I1, D1, F1 universe enforcement, unique league team codes)
ODDS_HIERARCHY_PARITY =
  VERIFIED (Avg -> BbAv -> Pinn close -> Pinn open -> B365 -> Max)
GATE_EVALUATION_HARNESS =
  VERIFIED (Block bootstrap, 2-price CLV, RPS, strictly beating baseline for PROCEED)
DURABLE_SUPABASE_LEDGER =
  VERIFIED (Append-only schema, authenticated RLS write policies, integrity constraints, fail-closed offline fallback)
DOMAIN_ENGINE_DECOUPLING =
  VERIFIED (DomainPredictionEngine, Pydantic AppConfig, FastAPI REST API service under api/main.py)
```

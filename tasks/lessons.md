# Lessons Learned — Draw Value Prediction Engine (DVPE)

> **2026-09-20 audit correction:** The historical VERIFIED labels below do not establish current correctness or production readiness. See the [source audit](../docs/audit-2026-09-20.md) for mathematical, fallback, identity, ledger and evidence-gate defects. A fixed optimizer evaluation budget does not guarantee convergence; continuing after a storage/provider failure is not fail-closed.

Forensic incident history, empirical modeling discoveries, architectural invariants, and anti-patterns encountered during development.

---

## Session: 2026-09-20 - Project Organization & Agent Brain Baseline

### Canonical Architectural Principles & Invariants
- **PYTHON_NAMESPACE_SHADOWING**:
  - Root-level directories matching third-party package names (e.g. `supabase/` folder next to `app.py`) can shadow installed site-packages when running scripts or tests with the workspace root on `sys.path`.
  - In unit tests where the library may not be installed globally, mocking must patch the consuming module (`src.tracking.supabase_ledger.create_client`) or inject into `sys.modules` rather than assuming global namespace resolution.
- **IDENTIFIABILITY_AND_SUM_TO_ZERO_BASIS**:
  - Unconstrained Poisson models produce an infinite set of collinear rating vectors $(\alpha + c, \beta + c)$.
  - Pinned reference coding $(\alpha_{\text{ref}} = 0, \beta_{\text{ref}} = 0)$ during L-BFGS-B optimization stabilizes the gradient.
  - Re-centering post-fit ratings to zero mean and absorbing the shift into $\mu_0$ leaves all goal intensities $\lambda, \mu$ mathematically unchanged while restoring interpretable zero-centered ratings.
- **PROFILED_RHO_STABILITY**:
  - The Dixon-Coles low-score parameter $\rho$ only affects four score cells: $(0,0), (1,0), (0,1), (1,1)$.
  - Joint optimization of $\rho$ alongside $2N + 2$ team and league parameters causes severe numerical instability and slow convergence.
  - Profiling $\rho$ via an outer 1-D bounded search (Brent's method over candidate $\rho \in [-0.15, 0.10]$) guarantees convergence in under 15 evaluations.
- **SMOOTH_SHRINKAGE_VS_DISCONTINUOUS_CLIFF**:
  - Hard match count thresholds (e.g. zeroing ratings for teams with $<15$ matches) created disruptive discontinuities where a 14-match team had 0 rating and a 15-match team had full rating.
  - Continuous ridge shrinkage weight $\frac{N_{\min}}{N}$ penalizes sparse teams smoothly toward the league average without abrupt cliff edges.

---

## Session: 2026-09-18 - Streamlit Cache & League Boundary Invariant Closure

### Canonical Architectural Principles & Invariants
- **STREAMLIT_CACHE_DATA_TTL_INVARIANT**:
  - Functions fetching or processing live fixtures cached with `@st.cache_data` without explicit `ttl` parameters retain stale data indefinitely across page refreshes and server days.
  - All fixture and odds-fetching functions MUST declare explicit `ttl` windows (e.g. `ttl=3600` for fixtures, `ttl=900` for live odds).
- **FIXTURE_LEAGUE_LEAKAGE_INVARIANT**:
  - Ingested upcoming fixtures from multi-competition feeds (such as The Odds API or football-data fixture sheets) can contain cups or leagues outside the Big 5 training universe.
  - Prediction pipelines and UI filters MUST strictly discard unmodeled league identifiers prior to running team-name normalization or simulation.

---

## Session: 2026-09-15 - Multi-Source Blending & Season-Aware Odds Invariants

### Canonical Architectural Principles & Invariants
- **ODDS_ARCHIVE_SCHEMA_DRIFT**:
  - Over 20+ years of historical data from football-data.co.uk, column names for consensus odds changed from `BbAv` (Betbrain) to `Avg`, and Pinnacle lines were introduced mid-history.
  - Hardcoded single-column fallbacks silently starve older seasons of odds data.
  - The loader must implement a multi-tier fallback hierarchy: `Avg` -> `BbAv` -> `Pinnacle Close` -> `Pinnacle Open` -> `Bet365` -> `Max`.
- **IMPLAUSIBLE_ODDS_TOLERANCE**:
  - Corrupt odds triples ($\le 1.01$ or non-positive) should be nulled to `NaN` rather than dropping the match row entirely. A match without valid market odds is still valid training data for goal-scoring intensity.

---

## Session: 2026-09-20 - Mathematical Audit, Data Integrity & Engine Decoupling

### Canonical Architectural Principles & Invariants
- **DIXON_COLES_TAU_ASYMMETRY**:
  - Dixon-Coles (1997) Eq (2.1) defines $\tau(0,1) = 1 + \lambda \rho$ and $\tau(1,0) = 1 + \mu \rho$.
  - Inverted assignment violates marginal Poisson conservation when $\lambda \ne \mu$.
  - Low-score adjustment must scale $\tau(0,1)$ with $\lambda$ (home intensity) and $\tau(1,0)$ with $\mu$ (away intensity).
- **ZERO_SILENT_DATA_FALLBACK_INVARIANT**:
  - Under no circumstances may an unavailable odds or fixture provider fall back silently to synthetic demo or static local CSV data in production.
  - Fail-closed behavior: return typed failure code (`FIXTURE_SOURCE_UNAVAILABLE`) with 0 recommendations.
- **CROSS_LEAGUE_IDENTITY_COLLISION**:
  - Team abbreviations must never clash across competitions (`MNZ` for Monza in Serie A, `MON` for Monaco in Ligue 1).
  - Models must fit in strict league isolation.
- **STRICT_STATISTICAL_GATE_APPROVAL**:
  - The evaluation harness must reject models whose flagged recommendations underperform baseline CLV, even if lower CI > 0.
- **REST_ENGINE_SEPARATION**:
  - Domain modeling logic must live in headless, UI-agnostic engine services (`src.engine.DomainPredictionEngine`) with typed schemas (`src.config.AppConfig`), exposed via FastAPI (`api.main`).

---

## Incident & Verification Ledger

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
RESPONSIBLE_GAMBLING_UI =
  VERIFIED (Prominent disclaimers, forecast-only badges on cards)
```

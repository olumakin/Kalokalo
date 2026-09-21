# System & Quantitative Invariants

> **2026-09-20 review:** These are historical intended rules, not verified guarantees. Several equations and behavior claims differ from code; see [audit A01–A20](audit-2026-09-20.md). In particular, graceful substitution to demo/cache/another model is not fail-closed. The current requested production target prohibits automatic source/model/price substitution and requires explicit unavailable states. No code has been changed to enforce that target yet.

Core invariants governing the Draw Value Prediction Engine (DVPE). Every code change, refactor, and data pipeline must preserve these rules.

---

## 1. Mathematical & Statistical Invariants

### 1.1 Identifiability & Basis Invariance (N-1 Constraint)
- The Poisson model intensity $\ln \lambda = \mu_0 + \gamma + \alpha_i - \beta_j$ has an unconstrained degree of freedom.
- **Invariant**: The optimizer MUST fix one well-observed reference team at $(\alpha_{\text{ref}} = 0, \beta_{\text{ref}} = 0)$ during likelihood maximization.
- Post-optimization, ratings MUST be re-centered onto a zero-mean basis ($\sum \alpha = 0, \sum \beta = 0$) with shifts absorbed into $\mu_0$:
  $$\alpha_i \leftarrow \alpha_i - \bar{\alpha}, \quad \beta_j \leftarrow \beta_j - \bar{\beta}, \quad \mu_0 \leftarrow \mu_0 + \bar{\alpha} - \bar{\beta}$$
  This basis transformation is mathematically exact and leaves all fitted goal intensities $\lambda_k, \mu_k$ identical.

### 1.2 Probability Space Preservation
- The 10x10 scoreline probability grid $P_{x,y}$ must satisfy:
  $$\sum_{x=0}^{9} \sum_{y=0}^{9} P_{x,y} \approx 1.0 \quad (\text{residual tail } < 10^{-4})$$
- 1X2 market probabilities derived from the matrix must sum to 1:
  $$p_{\text{home}} + p_{\text{draw}} + p_{\text{away}} = 1.0$$
- Low-score tau adjustment parameter $\rho$ must remain bounded within $(-0.15, 0.10)$ to guarantee strictly non-negative probabilities in $(0,0), (1,0), (0,1), (1,1)$.

### 1.3 Continuous Regularization
- Teams with sparse data ($N < N_{\min}$, default 15 matches) MUST NOT suffer discontinuous hard-zero cutoffs.
- Ratings must shrink smoothly toward league baseline ($0.0$) using continuous ridge weight $\frac{N_{\min}}{N}$.

---

## 2. Data & Temporal Integrity Invariants

### 2.1 Zero Lookahead / Temporal Leakage
- Any backtest or evaluation step for match at timestamp $t$ MUST ONLY fit parameters on matches where:
  $$\text{match\_date} < t$$
- Future odds movements, subsequent results, or league-table states occurring at or after $t$ MUST NEVER enter training or feature arrays.

### 2.2 Season-Aware Odds Trust Order
- When resolving historical match prices, the selection pipeline MUST adhere strictly to the trust hierarchy:
  1. Multi-book market average (`AvgH/D/A`)
  2. Legacy Betbrain market average (`BbAvH/D/A`)
  3. Pinnacle closing line (`PSCH/D/A`)
  4. Pinnacle opening line (`PSH/D/A`)
  5. Bet365 line (`B365H/D/A`)
  6. Market maximum (`MaxH/D/A` / `BbMxH/D/A`) — last resort only.
- Fabricated or synthetic placeholder odds are strictly prohibited; missing legs must yield `NaN` or row drops.

### 2.3 Fixture League Boundary
- The prediction engine must never evaluate fixtures from competitions outside the trained model's league universe (`E0`, `SP1`, `I1`, `D1`, `F1`).
- Active competition filters must strictly reflect calibrated leagues.

---

## 3. Financial & Risk Invariants

### 3.1 Dual Exposure Caps
- **Single-Match Cap**: No individual recommendation may exceed $2.5\%$ of current bankroll.
- **Daily Portfolio Cap**: The aggregate stake across all concurrent matches on a matchday slate MUST NOT exceed $8.0\%$ of bankroll.
- If $\sum \text{stake}_i > 0.08$, all individual stakes must scale down proportionally:
  $$\text{stake}_i \leftarrow \text{stake}_i \times \frac{0.08}{\sum \text{stake}_j}$$

### 3.2 Quarter-Kelly Default
- Position sizing uses Fractional Kelly with multiplier $f^* = 0.15$ (bounded by $[0.10, 0.25]$) to protect against parameter uncertainty and distribution drift.
- Full Kelly sizing ($f^* = 1.0$) is strictly prohibited.

### 3.3 Minimum Edge Threshold
- A bet qualifies only if Expected Value strictly exceeds the hurdle rate:
  $$\text{EV} = p_{\text{model}} \cdot o_{\text{market}} - 1 \ge 0.03 \quad (+3.0\%)$$

---

## 4. Architectural & Compliance Invariants

### 4.1 Append-Only Audit Durability
- Ledgers (`src/tracking/ledger.py` and `src/tracking/supabase_ledger.py`) are strictly append-only.
- Remote database permissions on the publishable key must enforce Row Level Security (RLS) allowing only `INSERT` and `SELECT`. `UPDATE` and `DELETE` operations are prohibited.
- Match settlement is recorded as a new append-only entry in `settlements`, never as an in-place mutation of the original prediction record.

### 4.2 Non-Financial Advice Boundary
- The user interface must prominently display the "NOT FINANCIAL ADVICE" compliance disclaimer and Responsible Gambling advisory links (`RG_URL`).
- Primary score-predictor match cards must present objective forecasts and tie-potential ratings, withholding stake amounts and financial badges from the primary consumer view.

### 4.3 Fail-Closed Network Resilience
- Missing or unreachable external dependencies (The Odds API, football-data.co.uk, Supabase, Understat) MUST degrade gracefully to local cached state or offline demo mode without raising unhandled exceptions or halting application boot.

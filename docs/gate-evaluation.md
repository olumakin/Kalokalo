# Gate Evaluation Harness — WP3 Evidence Pack

> **2026-09-20 review:** This document describes intended evidence methodology. Current code does not enforce above-baseline CLV, does not wire real-data entry/close assembly into ingestion, and does not enforce gate approval in production recommendations. See [audit A06, A10, A14 and A16](audit-2026-09-20.md). No full-corpus result or production authorization is established by these specifications.

Statistical evidence pack and quantitative decision harness for model deployment and market viability.

---

## 1. Objectives & Decision Gate

The gate harness (`src/validation/gate_harness.py`) determines whether the Draw Value Prediction Engine demonstrates genuine predictive edge or should be repositioned.

### Decision Rules (`src/validation/gate_report.py`)
1. **`PROCEED`**:
   - At least one league demonstrates a strictly positive lower bound on Closing Line Value ($95\%\text{ CI lower} > 0.0\%$) and statistically significant edge above baseline.
   - Authorizes live deployment for cleared leagues.
2. **`REPOSITION_FORECASTING`**:
   - All leagues exhibit upper CLV bounds at or below zero ($95\%\text{ CI upper} \le 0.0\%$).
   - Formally transitions the project to an analytical score-forecasting tool rather than an alpha engine.
3. **`INCONCLUSIVE`**:
   - Confidence intervals cross zero or sample sizes are insufficient ($N \le 10$ flagged bets).
   - Requires expanded historical corpus or further parameter tuning.

---

## 2. Statistical Methodology

### 2.1 Two-Price Closing Line Value (CLV)
CLV measures whether the model captured odds superior to final sharp market consensus:
$$\text{CLV} = \frac{o_{\text{entry}}}{o_{\text{closing}}} - 1$$

- **Pinnacle Self-Comparison**: Opening Pinnacle price vs. Closing Pinnacle price avoids cross-book margin asymmetry.
- **Retail Comparison**: Captures recreational bettor realism using Bet365 lines.
- **Baseline Contrast**: CLV of model-flagged bets is benchmarked against the baseline CLV of all matches in the league to verify true selection edge.

### 2.2 Seeded Percentile & Block Bootstrap
- Resamples row *indices* rather than values, supporting multidimensional arrays and DataFrames.
- **Matchweek Block Bootstrap**: Resamples whole rounds to preserve intra-matchweek correlation, simultaneous kickoff dynamics, and closing line shifts.
- **Paired Bootstrap**: Computes Diebold-Mariano style differences for log-loss and RPS between competing model configurations.

### 2.3 Multi-Class Scoring: Log-Loss & RPS
- **Ranked Probability Score (RPS)**:
  Measures probabilistic accuracy on the ordered scale $\text{Home} < \text{Draw} < \text{Away}$:
  $$\text{RPS} = \frac{1}{2} \sum_{m=1}^{2} \left( \sum_{k=1}^m p_k - \sum_{k=1}^m y_k \right)^2$$
  where $y$ is the one-hot outcome vector. Unlike Brier score, RPS rewards predictions that are "closer" on the ordinal football outcome spectrum.

### 2.4 Cox Calibration Regression
- Fits log-odds regression:
  $$\text{logit}(P(Y=1)) = a + b \cdot \text{logit}(p_{\text{model}})$$
- Well-calibrated models satisfy $a = 0$ (no overall bias) and $b = 1$ (no over/under-confidence).

---

## 3. Caching & Performance Architecture

- **Disk Caching**: Fits are serialized to disk keyed by `(league, retrain_date, xi, git_hash)` so that repeated evaluations amortize compute cost.
- **Warm Starts across Seasons**: When retraining across season boundaries, existing team ratings are preserved while newly promoted clubs initialize at neutral $(0, 0)$ priors.
- **Pre-parsed Datetimes**: Date columns must arrive as UTC `datetime64` from ingestion, decoupling data parsing from the validation loop.

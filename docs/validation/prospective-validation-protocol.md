# Prospective Validation Protocol — Kalokalo DVPE

## Status & Baseline
* **Canonical Remediation Baseline**: `2c3c90031c485a0734e63d6fdc34fa9d8f78919a` (Permanent)
* **Epoch 1 Model SHA**: `UNASSIGNED` (Pending future validation freeze commit)
* **Epoch 1 Status**: `PRE_OBSERVATION_BLOCKED`
* **Prospective Denominator**: `0`
* **Model Tuning**: `FROZEN` (Locked through Epoch preparation and active observation)
* **Empirical Validity**: `INSUFFICIENT_EVIDENCE`
* **Edge Claim**: `NOT_AUTHORIZED`

---

## 1. Ratified Governance Clarifications
1. **Stage 3 Boundary**: Stage 3 (Forecast & Benchmark Capture Daemon) may achieve technical capture-readiness, but **may NOT begin prospective observation** or increment the prospective denominator until all pre-epoch gates are passed.
2. **Prediction Lead-Time Invariant**: Expressed relative to scheduled kickoff:
   $$t_{\text{pred}} \le t_{\text{kickoff\_sched}} - \text{decision\_lead\_time}$$
   where $\text{decision\_lead\_time} \ge 60\text{ minutes}$.
3. **Pre-Epoch Gates**: Prior to assigning `EPOCH_1_MODEL_SHA` and activating observation, explicit verification is required for:
   * Settlement-path verification
   * Provenance verification (Code, Config, and Contract identities)
   * Clock/timestamp-contract verification ($\Delta t_{\text{skew}} \le 120\text{s}$)
   * Protocol ratification & MUE freeze
   * A09 execution contract separation
   * Immutable append-only storage policy
4. **Model Tuning**: `MODEL_TUNING = FROZEN`. Zero parameter adjustments ($\xi$, home advantage, grid bounds, or optimizer tolerances) are permitted through epoch preparation and active observation.

---

## 2. Implemented Stage 1 Contracts

### 2.1 Provenance & Identity Engine (`src/validation/manifest.py`)
Every observation binds to the cryptographic identity triad:
* **`CODE_IDENTITY`**: 40-character Git commit hash verified via `git rev-parse HEAD`. Working tree dirty status is inspected and bound (`is_working_tree_dirty`).
* **`SEMANTIC_CONFIG_IDENTITY`**: Deterministic 64-character SHA-256 digest computed via canonical JSON serialization (`json.dumps(sort_keys=True, separators=(',', ':'))`). Covers all parameters affecting inference and decisions (`model`, `devig`, `edge`, `leagues`, `team_mappings`).
* **`EXTERNAL_CONTRACT_IDENTITY`**: Explicitly versioned contracts:
  * `PriceSourceContract::v1.0`
  * `SettlementContract::v1.0`
  * `ClosingLineContract::v1.0`
  * `FixtureLifecycleContract::v1.0`

### 2.2 Immutable Append-Only Event Store (`src/tracking/events.py` & `supabase/schema.sql`)
* **Event Types**: `fixture_lifecycle`, `prediction_record`, `market_benchmark_snapshot`, `execution_quote_snapshot`, `decision_record`, `closing_line_snapshot`, `settlement_record`, `amendment_event`, `integrity_incident`.
* **Idempotency Key**: Deterministic 64-character SHA-256 hash computed over canonical event identity:
  $$\text{idempotency\_key} = \text{SHA256}(\text{entity\_id} \mid \text{event\_type} \mid \text{provider\_event\_id} \mid \text{qualifier} \mid \text{timestamp})$$
* **Storage Invariants**:
  * Duplicates with identical payload are ignored idempotently.
  * Collisions with conflicting payload raise `DuplicateIdempotencyCollisionError`.
  * Destructive mutations (`UPDATE` / `DELETE`) are strictly forbidden. Amendments append new records pointing to `target_event_id`.
  * Test events are tagged `test_flag = TEST_ONLY_NON_PROSPECTIVE` and strictly excluded from prospective denominator listings.
* **Local Ledger Role**:
  * `src/tracking/ledger.py` functions as local developer read replica, offline cache, and export tool only.
  * Preserves documented limitation: `CONCURRENT_LOST_UPDATE_UNPROTECTED` (due to atomic file replacement without OS locking).

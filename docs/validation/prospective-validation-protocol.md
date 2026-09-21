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

---

## 3. Implemented Stage 2 Contracts: Authoritative Fixture Tracker (`FixtureLifecycleContract::v1.0`)

### 3.1 Canonical Fixture Identity & Uniqueness Invariant
* **Natural-Key Role**: Natural-key attributes (`competition_id`, `season_id`, `home_team_id`, `away_team_id`) constitute **reconciliation evidence**, NOT the sole fixture identity.
* **Internal Fixture ID**: The authoritative registry assigns an immutable internal fixture identifier:
  $$\text{internal\_fixture\_id} = \text{fix\_}\{\text{clean\_comp}\}\_\{\text{SHA256}(\text{comp} \mid \text{season} \mid \text{home} \mid \text{away} \mid \text{occurrence\_index})[:24]\}$$
  where `occurrence_index` is a registry-managed monotonic index ($\ge 1$). Two distinct fixtures sharing identical competition, season, and team pairings (e.g. domestic cup replays or split-season tournament legs) receive distinct internal fixture IDs without relying on caller intervention.
* **Provider Identity & Aliasing**: Provider event IDs (`provider_namespace:provider_event_id`) map to exactly one internal fixture ID. When a provider replaces its event ID after match rescheduling, both provider IDs link to the same internal fixture ID and all historical aliases are preserved.
* **Fail-Closed Ambiguous Reconciliation**: When an incoming unseen provider event matches multiple active candidate fixtures, or when alias linkage is explicitly disabled, the registry refuses to guess and emits `FIXTURE_RECONCILIATION_REQUIRED` (`ReconciliationRequiredError`). Automatic merge is strictly prohibited under ambiguous evidence.
* **Cross-Competition & Display Name Isolation**: Competitions strictly isolate fixtures; provider events cannot silently migrate across leagues (`IdentityConflictError`). Formatting, accents, Unicode representation, and display labels have zero impact on canonical identity, which is governed strictly by normalized canonical IDs.

### 3.2 Fixture Lifecycle State Machine
* **States**: `SCHEDULED`, `POSTPONED`, `RESCHEDULED`, `STARTED`, `ABANDONED_PENDING_RULING`, `COMPLETED`, `OFFICIAL_RESULT_STANDS`, `VOIDED`.
* **Legal Transitions**:
  * `SCHEDULED` $\to$ `POSTPONED`, `RESCHEDULED`, `STARTED`, `VOIDED`
  * `POSTPONED` $\to$ `RESCHEDULED`, `VOIDED`
  * `RESCHEDULED` $\to$ `SCHEDULED`, `STARTED`, `POSTPONED`, `VOIDED`
  * `STARTED` $\to$ `COMPLETED`, `ABANDONED_PENDING_RULING`, `VOIDED`
  * `ABANDONED_PENDING_RULING` $\to$ `OFFICIAL_RESULT_STANDS`, `VOIDED`
  * `COMPLETED` $\to$ `OFFICIAL_RESULT_STANDS`, `VOIDED`
  * Terminal absorbing states: `OFFICIAL_RESULT_STANDS`, `VOIDED` (zero forward transitions; subsequent stale events are rejected fail-closed).

### 3.3 Event Durability, Ordering, and Stage 1 Foundation
* **Authoritative Persistence**: `STAGE_1_EVENT_STORE = AUTHORITATIVE_PERSISTENCE`. Stage 2 lifecycle events are persisted as `EventType.FIXTURE_LIFECYCLE` within the Stage 1 immutable event store. Zero parallel event ledgers exist.
* **Durable Ordering Semantics**: Event streams explicitly distinguish:
  1. `provider_event_timestamp_utc`: External publisher timestamp.
  2. `fixture_event_sequence`: Monotonic 1-based sequence within the fixture.
  3. `ingestion_timestamp_utc`: Local ingestion timestamp.
* **Deterministic Projection Replay**: Derived current-state projections (`FixtureStateProjection`) are reproducible from the immutable event store. Ordering is evaluated across `(provider_event_timestamp_utc, fixture_event_sequence, created_at_utc, event_id)`. Contradictory history or illegal state sequences fail closed immediately.
* **Non-Prospective Invariant**: Every Stage 2 lifecycle event is tagged `test_flag = TEST_ONLY_NON_PROSPECTIVE`. Fixture lifecycle infrastructure alone cannot increment `PROSPECTIVE_DENOMINATOR` (remains `0`).

### 3.4 Kickoff Semantics
* `scheduled_kickoff_utc`: Latest scheduled match time. Schedule history tracks all historical adjustments.
* `actual_kickoff_utc`: Actual whistle time. **Never inferred** from scheduled kickoff or elapsed wall-clock time; requires explicit provider or official status (`UNKNOWN`, `PROVIDER_REPORTED`, `OFFICIALLY_VERIFIED`).

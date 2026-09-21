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

---

## 4. Implemented Stage 3 Contracts: Pre-Match Forecast & Benchmark Capture

### 4.1 Capture Modes & Non-Prospective Gates
* **Modes**: `TEST`, `PRE_EPOCH_DRY_RUN`, `PROSPECTIVE`.
* **Pre-Epoch Guard**: Any attempt to emit `PROSPECTIVE` evidence while `EPOCH_1_MODEL_SHA = UNASSIGNED` raises `ProspectiveActivationBlockedError` fail-closed.
* **Non-Prospective Evidence Invariant**: All Stage 3 records are tagged `test_flag = TEST_ONLY_NON_PROSPECTIVE`, `prospective_eligible = False`, and `evidence_classification = "PRE_EPOCH_VALIDATION"`.
* **Prospective Denominator Guard**: `PROSPECTIVE_DENOMINATOR` remains strictly `0`.

### 4.2 Forecast Capture Daemon (`ForecastCaptureContract::v1.0`)
* **Authoritative Fixture Consumption**: Consumes fixtures exclusively through Stage 2 `FixtureStateProjection` by `internal_fixture_id`.
* **Timing Contract (`ForecastTimingContract::v1.0`)**: Evaluates $t_{\text{pred}} < t_{\text{kickoff\_sched}} - \text{decision\_lead\_time}$ (default 60 minutes). Predictions attempted at or after cutoff are recorded as `POST_CUTOFF_ATTEMPT` with `model_probabilities = None`.
* **Model Failure Auditing**: Typed failures (`UNKNOWN_TEAM`, `INSUFFICIENT_HISTORY`, `MODEL_NON_CONVERGENCE`, `POST_CUTOFF_ATTEMPT`, `UNMODELED_LEAGUE`) append auditable records into the Stage 1 store. Zero fabricated probabilities; zero silent skips.
* **Forecast-Market Decoupling**: Forecast records persist independently of odds availability or market provider outages.

### 4.3 Market Benchmark Daemon (`MarketBenchmarkContract::v1.0`)
* **Reference Consensus Only**: Captures contemporaneous 1X2 market quotes as benchmark evidence. Strictly tagged `is_executable_price = False` (never executable, zero betting decisions, zero Kelly sizing, zero PnL/CLV). Stage 3 does NOT implement A09.
* **Raw & Derived Reproducibility**: Persists `raw_odds_home`, `raw_odds_draw`, `raw_odds_away`, and `devig_method`, enabling 100% forensic recomputation of `derived_market_p_*`.
* **Typed Failures**: Source outages or invalid odds emit append-only failure records (`MARKET_SOURCE_UNAVAILABLE`, `MARKET_ODDS_INVALID`, `MARKET_PAYLOAD_INVALID`, `MARKET_TIMESTAMP_INVALID`) without mutating predictions.
* **Temporal Validation**: Rejects quotes with >120s future clock skew or quotes timestamped after a known actual kickoff.

### 4.4 Fixture Join Invariant
* Forecast predictions and market benchmark snapshots join **exclusively** on Stage 2 `internal_fixture_id`. Human-readable labels, team display formatting, and mutable date strings are strictly prohibited as join keys.

---

## 5. Implemented Stage 4 Contracts: Named Execution Feed & Paper Decision Daemon

### 5.1 Architecture & Economic Separation (A09)
Stage 4 implements the A09 decision-semantic correction separating three distinct price and evaluation concepts:
1. **Market Benchmark Quote (`is_executable_price = False`)**: Captured by `MarketBenchmarkDaemon` under `MarketBenchmarkContract::v1.0`. Consensus/reference odds used exclusively for the information filter:
   $$\text{model\_p\_draw} > \text{benchmark\_market\_p\_draw}$$
2. **Available Execution Quote (`is_executable_price = True`)**: Captured by `ExecutionQuoteDaemon` under `ExecutionQuoteContract::v1.0`. Observable, named bookmaker prices used exclusively for executable EV and position sizing:
   $$\text{EV} = (\text{model\_p\_draw} \times \text{execution\_decimal\_odds}) - 1 \ge \text{min\_ev}\;(+0.03)$$
3. **Closing Reference Quote**: Reserved for Stage 5. Zero closing-line evaluation, zero CLV calculations, and zero settlement records are generated in Stage 4.
4. **Strict Zero-Fallback Policy**: If an observable named execution quote is absent, invalid, or stale, the decision fails closed (`NO_EXECUTION_QUOTE` or explicit error code) and yields `constrained_paper_stake_fraction = 0.0`. Under no circumstances may consensus benchmark odds be substituted into execution fields.

### 5.2 Named Execution Feed (`ExecutionQuoteContract::v1.0`)
* **Authoritative Implementation**: `src/ingestion/execution_feed.py`.
* **Named Bookmaker Requirement**: Only verifiable, named bookmakers (e.g. `Bet365`, `Pinnacle`, `WilliamHill`) are eligible. All consensus, median, average, synthetic, composite, or anonymous sources are rejected fail-closed with `EXECUTION_SOURCE_NOT_NAMED`.
* **Timestamp Provenance**:
  * Provider quote timestamp (`provider_quote_timestamp_utc`) is required. Absence marks quote as `UNKNOWN` and rejects execution eligibility (`EXECUTION_QUOTE_TIMESTAMP_UNKNOWN`).
  * Ingestion timestamp is strictly separated and never substituted for quote timestamp.
* **Freshness & Causal Order Invariants**:
  * Quote age must satisfy:
    $$t_{\text{ingest}} - t_{\text{provider\_quote}} \le \text{max\_quote\_age\_seconds}\;(900\text{s})$$
    Quotes exceeding the freshness limit fail with `EXECUTION_QUOTE_STALE`.
  * Clock anomaly protection rejects quotes with $>120\text{s}$ future skew (`EXECUTION_QUOTE_FUTURE_TIMESTAMP`).
  * Pre-match execution timing rejects quotes timestamped after a known actual kickoff (`EXECUTION_QUOTE_POST_KICKOFF`).
* **Odds Validity**: Decimal odds must strictly satisfy $\text{odds} > 1.0$, non-NaN, and finite (`EXECUTION_ODDS_INVALID`).
* **Authoritative Persistence**: Captured snapshots are persisted as append-only `EventType.EXECUTION_QUOTE_SNAPSHOT` in the Stage 1 store with deterministic SHA-256 idempotency keys.

### 5.3 Paper Decision Daemon (`PaperDecisionContract::v1.0`)
* **Authoritative Implementation**: `src/workers/decision_daemon.py`.
* **Frozen Decision Input Triad**: Binds:
  1. Authoritative Stage 2 fixture projection (`FixtureStateProjection`).
  2. Stage 3 frozen model prediction record (`EventType.PREDICTION_CAPTURE_SUCCESS`), including `model_fit_identity` and `training_data_identity`.
  3. Stage 3 market benchmark snapshot (`EventType.MARKET_BENCHMARK_SNAPSHOT`) for information filtering.
  4. Stage 4 named execution quote snapshot (`EventType.EXECUTION_QUOTE_SNAPSHOT`) for EV and sizing.
* **Timing & Lifecycle Gating**:
  * Pre-match cutoff: $t_{\text{decision}} < t_{\text{kickoff\_sched}} - \text{decision\_lead\_time}$ (rejected fail-closed as `POST_CUTOFF_DECISION_ATTEMPT` at or after cutoff).
  * Lifecycle state: Fixture must be in `SCHEDULED` or `RESCHEDULED` state. Ineligible states (`POSTPONED`, `STARTED`, `COMPLETED`, `VOIDED`) disqualify the decision fail-closed.
  * Causal timeline: Quote and forecast timestamps must strictly precede or equal decision timestamp:
    $$t_{\text{quote\_provider}} \le t_{\text{quote\_ingest}} \le t_{\text{decision}} < t_{\text{cutoff}}$$
* **Position Sizing & Dual-Layer Exposure Capping**:
  * **Fractional Kelly**: Evaluated on execution price with fixed multiplier $c = 0.15$:
    $$f^* = 0.15 \times \frac{b \cdot p - q}{b},\quad b = \text{odds}_{\text{exec}} - 1,\quad p = \text{model\_p\_draw},\quad q = 1 - p$$
  * **Single-Match Cap**: Stake fraction clipped to $\min(f^*, 0.025)$.
  * **Daily Slate Cap**: Daily cumulative paper exposure capped at $\le 0.080$. Remaining slate capacity dynamically bounds each subsequent decision on the slate. Once cumulative exposure reaches $0.080$, capacity is exhausted and additional bets receive a stake fraction of $0.0$.
  * **Deterministic Exposure Snapshot**: Each decision record embeds an immutable `exposure_snapshot` capturing `slate_id`, `preceding_decision_ids`, `prior_cumulative_exposure`, and `post_decision_cumulative_exposure`. Replaying identical decision streams across independent environments yields bitwise identical exposure metrics.

### 5.4 Governance & Non-Prospective Invariants
* **Strict Non-Prospective Evidence**: Every Stage 4 record is tagged `test_flag = TEST_ONLY_NON_PROSPECTIVE`, `prospective_eligible = False`, `evidence_classification = "PRE_EPOCH_VALIDATION"`, and `is_paper_decision = True`.
* **Prospective Denominator Guard**: `PROSPECTIVE_DENOMINATOR` remains strictly `0`.
* **Pre-Epoch Activation Guard**: Any attempt to run either daemon with `capture_mode = PROSPECTIVE` while `EPOCH_1_MODEL_SHA = UNASSIGNED` raises `ProspectiveActivationBlockedError`.
* **Paper Stakes Only**: All sizing values are paper simulations. No real money bets are placed, and zero claims of liquidity or fills are made.

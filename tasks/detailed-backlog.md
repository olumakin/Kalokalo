# Detailed task specifications and quality gates

Updated: 2026-09-20. All 58 tasks are **open and not implemented**. This document expands all 44 audit IDs and the migration, verification, empirical-validation and future backlog work. It is a documentation change, not authorization to execute production deployments, send notifications, or place bets.

## Shared definition of done

Each task must deliver its scoped change, pass its own acceptance criteria and quality gate, and attach evidence before its status changes. A task that documents a limitation or feasibility decision may finish with an honest negative decision; it must not invent success or imply release approval. A passing unit test alone does not establish empirical model value.

Required closure record: task ID, owner/reviewer, change/commit reference, tested build/config/data identities, exact checks and observed results, remaining limitations, evidence links, and closure date. Until populated, status stays open. Record N/A with a reason for genuinely irrelevant checks; do not waive a named gate simply because its tools or data are unavailable.

Gate outcomes are pass, fail, or not run. Fail/not run keeps the task open. Dependencies below are completion prerequisites; preparatory work may start earlier. A lower-numbered priority can depend on a prerequisite at another priority: for example, establish the A20 test environment before verifying mathematical repairs. Priority denotes risk, not a rigid execution schedule.

For each implementation change: run focused regressions, affected integration/contract tests, and required security/data checks. For UI changes, verify rendered states and accessibility; for storage changes, verify migration/concurrency/recovery; for numerical changes, compare independent analytic cases before normalization and rerun impacted evidence. Do not claim passing tests when merely parsed for syntax.

No silent provider/model/price substitution, fabricated production observations, or invented provenance is accepted. Test fixtures may be synthetic only in isolated test environments. Missing required real data yields explicit unavailable status and blocks dependent publication.

Where a task needs empirical limits (freshness, sample size, tail tolerance, drift thresholds, recovery objectives, performance budget), the first deliverable is a versioned policy with numerical thresholds and rationale, recorded before the validation run. Passing by changing a threshold after observing the result is prohibited. This document does not fabricate operational values without data.

## Release-level gates

| Gate | Required evidence | Blocks |
|---|---|---|
| G1 — Numerical and data correctness | A01–A05, A08–A10, A13–A14, validated config and regression reports | Any production recommendation |
| G2 — Empirical approval | A06, A16, R01/R02, immutable report and model approval; xG requires A18 separately | Recommendation eligibility for that exact league/version |
| G3 — Security and durable operation | A07/A11/A12, IMP01–IMP07, M03, restore/restart/concurrency evidence; A15 if positions enabled | Multi-user production publication |
| G4 — User experience | UX01–UX12, QA01/QA02 rendered/user evidence | Public frontend cutover |
| G5 — Controlled release | M05/M06 readiness, explicit release authorization, rollback and one-writer verification | Traffic switch/deployment |

These are software/release gates, not legal compliance certification. Forecast-only operation must still satisfy numerical validity, provenance, security and honest-status requirements; a negative empirical edge decision must keep recommendations disabled.

## P0 tasks
<a id="a01"></a>
### A01 — P0 — Dixon–Coles low-score factors are reversed

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A20](#a20).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Correct the factors; test both asymmetric cells with unequal goal rates, untruncated mass cancellation, marginal preservation, and training/inference agreement. Invalidate model caches and rerun historical evidence after correction.

**Acceptance criteria:**

- Unequal home/away rates give the correct factors in all four low-score cells.
- Untruncated corrections conserve mass and marginals.
- Corrected inference agrees with its likelihood.

**Quality gate:** Independent analytic regression cases pass before grid normalization; invalidate affected artifacts and attach the numerical comparison.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a02"></a>
### A02 — P0 — Synthetic and static observations can produce ordinary recommendations

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A20](#a20).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Remove synthetic/sample providers from the production dependency path. If retained for tests, package them separately and prohibit production imports. Unavailable selected sources must yield an explicit unavailable result and no recommendation. Every published prediction must reference verified historical and market snapshots. Failure-injection tests must produce zero replacement fixtures and zero production predictions.

**Acceptance criteria:**

- Production cannot import or select synthetic/sample providers.
- Source failures create zero replacement forecasts.
- Every published row references observed input snapshots.

**Quality gate:** Failure-injection tests for absent key, outage, empty provider and sample-file availability all block dependent publication; inspect the production package.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a03"></a>
### A03 — P0 — Cross-league team codes merge distinct clubs

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A20](#a20).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Use stable team IDs scoped by competition (provider IDs mapped to internal IDs) and fit one model per league. Route fixtures to their league's model. Verify that adding F1 history cannot change an I1 model or fixture prediction, and that both MON clubs remain distinct.

**Acceptance criteria:**

- Monza and Monaco have distinct IDs.
- Adding French history cannot change an Italian fit.
- Wrong-league fixtures and unresolved identities are rejected.

**Quality gate:** League-isolation and identity-collision regressions pass for live and evaluation entrypoints.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a04"></a>
### A04 — P0 — Failed, substituted, or unknown-team models remain eligible

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A01](#a01), [A03](#a03).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Return a typed failed-fit/unknown-team result; do not substitute independent Poisson or neutral ratings in production. Require successful inner and outer optimization, finite parameters, approved model version, and supported identities before inference or qualification. Test each failure state through both API and evaluation paths.

**Acceptance criteria:**

- Inner or outer fit failure creates no eligible artifact.
- Unknown teams create no prediction.
- NaN or infinite parameters fail validation.

**Quality gate:** Force each optimizer/identity failure through inference and evaluation; verify no independent-Poisson or neutral-rating substitution.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a05"></a>
### A05 — P0 — Invalid probability models are repaired by clipping

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A01](#a01).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Constrain admissible rho jointly with rates, reject nonfinite/negative probability states, and monitor retained mass before normalization. Use an adaptive grid with a declared tail tolerance and hard resource limit; reject unsupported cases at that limit. Test extreme rates, boundary rho, and all four corrected tau cells.

**Acceptance criteria:**

- All four factors are admissible before normalization.
- Negative/nonfinite cells fail explicitly.
- Retained mass is checked against a declared tolerance.

**Quality gate:** Boundary-rate/rho and high-tail cases pass independent probability checks; record tolerance and resource-limit behavior before testing.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="m01"></a>
### M01 — P0 — Establish a shared strict domain engine

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A01](#a01), [A02](#a02), [A03](#a03), [A04](#a04), [A05](#a05), [A13](#a13), [IMP04](#imp04).

**Source:** [review context](../docs/frontend-backend-migration.md).

**Scope and deliverables:** Extract corrected league-scoped fit/inference, input specifications and typed unavailable states into UI-independent Python services. Make CLI and future workers use them. Preserve actual-data provenance and prohibit silent provider/model substitutions.

**Acceptance criteria:**

- Core domain imports no Streamlit or storage clients.
- Identical specifications give parity across entrypoints.
- Invalid models and missing required data create no eligible outputs.

**Quality gate:** Architecture/import-boundary checks and cross-entrypoint numerical/failure contract tests pass.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

## P1 tasks
<a id="a06"></a>
### A06 — P1 — Gate can approve a selection worse than baseline

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A10](#a10), [A14](#a14), [A16](#a16).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Estimate a joint matchweek-block bootstrap confidence interval for flagged-minus-baseline CLV on the same resampled weeks, preserving their overlap. Require both positive absolute flagged CLV and positive incremental CLV, sufficient independent weeks, and finite diagnostics. An approved report must be tied to an exact model/data/config version and enforced by the recommendation service. Add the counterexample as a regression case; failed/inconclusive evidence must never authorize recommendations.

**Acceptance criteria:**

- A positive flagged CLV below baseline cannot approve release.
- Missing/invalid intervals remain inconclusive.
- Approval matches exact model/data/config identities.

**Quality gate:** The published worse-than-baseline counterexample is rejected; joint block-bootstrap comparisons and approval-version mismatch tests pass.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a07"></a>
### A07 — P1 — Audit records are forgeable under the shipped anonymous policies

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A20](#a20).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Deny anonymous writes, authenticate users, and authorize compute/settlement operations server-side. Use least-privilege database roles and constraints. Test anonymous access, cross-user access, forged predictions, duplicate settlements, and invalid probabilities against an isolated database.

**Acceptance criteria:**

- Anonymous and unauthorized writes fail.
- Authorized workers insert only valid records.
- Cross-user reads/writes and forged settlements fail.

**Quality gate:** Run positive and negative authorization tests against an isolated real database including grants, RLS and constraints; mocked clients alone do not pass.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a08"></a>
### A08 — P1 — History can stay stale indefinitely; upcoming status is not enforced

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A13](#a13), [IMP03](#imp03).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Version and timestamp source snapshots, refresh active seasons, validate completeness, and block stale or already-started fixtures. Require explicit UTC forecast time and training cutoff, history strictly before cutoff, and the configured rolling window. Test active-season updates, stale snapshots, malformed fixtures, and historical replay without lookahead.

**Acceptance criteria:**

- Active-season changes appear in a new snapshot.
- Expired odds/past fixtures cannot publish.
- Training uses the declared cutoff and rolling window.

**Quality gate:** Clock-controlled stale, boundary-time and historical-replay tests pass with no later observations in training.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a09"></a>
### A09 — P1 — Consensus odds are treated as an executable price

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A13](#a13).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Keep complete timestamped bookmaker quotes. Separate a declared consensus estimator from the specific executable quote used for EV. Choose and record one explicit evaluation price policy; missing required legs exclude the observation with a reason instead of choosing a different tier. Evaluate fees and availability only where supported by real execution data.

**Acceptance criteria:**

- Store complete bookmaker quote identity and time.
- Consensus and executable price are distinct fields.
- Missing required price legs produce recorded exclusions.

**Quality gate:** Incomplete-book and mixed-time fixtures cannot masquerade as executable quotes; trace EV to one stored quote.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a10"></a>
### A10 — P1 — Historical ingestion does not assemble the gate's required data

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A13](#a13), [A09](#a09).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Build an explicit historical snapshot-to-evaluation dataset service that preserves both price legs and provenance. Test with representative archival CSV schemas, including absent required columns. Run out-of-sample evaluation only after A01–A09, and distinguish software smoke tests from empirical model evidence.

**Acceptance criteria:**

- Actual archival schemas retain entry/close legs through normalization.
- Missing-price reasons are counted.
- The harness consumes the resulting canonical dataset.

**Quality gate:** Run an ingestion-to-gate integration using representative archived provider schemas, not only a directly fabricated canonical DataFrame.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a11"></a>
### A11 — P1 — Settlement accounting conflates predictions with positions

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A12](#a12).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Separate immutable prediction snapshots, explicit position/execution records, and result events. Calculate return for each actual position using its own quote and stake. Forecast-only records must have no realized trading PnL. Add repeated-run, differing-odds, zero-stake, partial-settlement, and idempotency checks.

**Acceptance criteria:**

- Prediction-only rows generate no realized PnL.
- Each position uses its own odds/stake.
- Repeated settlements are idempotent and corrections preserve history.

**Quality gate:** Different-odds/stake, zero-stake, repeated-run and correction scenarios reconcile individual and total returns against hand calculations.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a12"></a>
### A12 — P1 — Local/remote ledgers diverge and cannot ensure durable publication

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A07](#a07), [A14](#a14).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Use one transactional Postgres source of truth, immutable run/prediction metadata and settlement events, and request idempotency. Expose results only after the authoritative transaction succeeds. Local CSV/Parquet becomes an explicit export. Preserve legacy imports with `provenance_unknown`; do not fabricate missing metadata.

**Acceptance criteria:**

- One authoritative transaction publishes run and predictions.
- Failures publish no success.
- Retries/restarts preserve one logical result.
- Legacy provenance stays unknown.

**Quality gate:** Database concurrency/restart/failure tests pass and UI/CLI readers agree; verify local files are exports, not a substitute writer.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a13"></a>
### A13 — P1 — Input and date validation is weaker than documentation claims

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A20](#a20).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Introduce strict boundary schemas with finite numeric constraints, explicit source date semantics, UTC event times, separately represented date-only historical records, stable identity resolution, and reasoned exclusions. Do not invent kickoff times from date-only data. Unknown identities must stop that fixture, not create a guessed club code. Validate raw data before coercion.

**Acceptance criteria:**

- Reject invalid/nonfinite/fractional goal counts and invalid prices.
- Preserve known UTC times versus date-only observations.
- Unknown teams cannot become guessed codes.

**Quality gate:** Boundary-schema corpus covers malformed, missing, blank, mixed-timezone, duplicate and unsupported-league inputs with deterministic reason codes.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a14"></a>
### A14 — P1 — Reproducibility and training semantics drift between paths

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A03](#a03), [IMP04](#imp04).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** One training specification and artifact hash must include data snapshot, cutoff, league, feature target, all configuration, code/build version, and dependency version. Use the same fit/inference services in live and evaluation paths. Convert warm starts into the optimizer basis or use a consistent centered parameterization. Verify cache invalidation and live/replay parity.

**Acceptance criteria:**

- All effective inputs affect artifact/cache identity.
- Changed datasets/settings invalidate reuse.
- Live and replay use identical cutoffs and model semantics.

**Quality gate:** Reproduce an artifact from its manifest; change each identity input independently; test reference-basis warm starts and dirty/unknown build identity handling.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a15"></a>
### A15 — P1 — Exposure limits apply only inside one run

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A11](#a11), [IMP04](#imp04).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Deduplicate fixture identities before qualification. Treat current percentages as hypothetical sizing until an authenticated portfolio and transactional exposure reservation exists. If position features remain in scope, enforce limits against open positions and the declared portfolio calendar in a database transaction.

**Acceptance criteria:**

- Duplicate fixtures do not multiply exposure.
- Limits include existing reservations/positions.
- Concurrent requests cannot exceed portfolio caps.

**Quality gate:** Transactional race and repeated-request tests enforce limits; no prediction is counted as an execution without a position record.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp01"></a>
### IMP01 — P1 — Exclude local secrets from image builds

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Explicitly exclude secret files, private keys, and local artifacts; preferably copy an allowlisted runtime tree. Supply runtime secrets through the deployment environment, and use build-secret mounts only if a build genuinely needs credentials. Keep production images free of test/demo observations.

**Acceptance criteria:**

- Local secret/private files cannot enter build context or distributable image layers.
- Runtime credentials remain injectable.
- Production artifacts exclude demo observations.

**Quality gate:** Build only an isolated sentinel fixture image during implementation; inspect context/layers for harmless markers, never real secrets.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp02"></a>
### IMP02 — P1 — Redact provider credentials from failure logs

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Log structured provider/league/status/reason fields rather than raw exceptions or URLs. Redact sensitive query parameters and headers in all adapters, error reporting, and UI diagnostics. Preserve a non-sensitive correlation ID for support.

**Acceptance criteria:**

- No sentinel key appears in logs, errors or reports under HTTP/transport failures.
- Safe provider/status/correlation fields remain useful.

**Quality gate:** Capture failure output for 401, 429, 500 and transport exceptions and assert sensitive parameters/headers are redacted.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp03"></a>
### IMP03 — P1 — Validate downloads before committing the historical cache

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Parse and validate a bounded candidate download before accepting it; publish a validated snapshot atomically. Quarantine corrupt cache entries with a reason and retry the same configured source according to policy. Preserve old snapshots for audit, not as silent substitutes for a requested fresh snapshot.

**Acceptance criteria:**

- Invalid/partial downloads never replace a validated snapshot.
- Corrupt cache reads fail predictably.
- Concurrent readers see complete snapshots only.

**Quality gate:** Inject HTML-200, truncated CSV, wrong encoding/schema and interrupted writes; verify atomic promotion and same-source recovery.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp04"></a>
### IMP04 — P1 — Resolve and validate configuration once

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Introduce a typed configuration schema with supported leagues/methods, positive integer limits, finite probabilities/caps, a declared Kelly range, and valid paths/source settings. Resolve defaults and overrides once at the boundary, inject the resolved configuration, and record its hash with each run. Reject invalid settings before downloading, fitting, or writing anything.

**Acceptance criteria:**

- Reject invalid caps/methods/leagues before side effects.
- Custom settings reach ingestion.
- One resolved config/hash is used throughout a run.

**Quality gate:** Parameterize boundary/NaN/infinite/negative configuration cases and test custom-source override propagation through CLI and service entrypoints.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp05"></a>
### IMP05 — P1 — Add a documented and tested restore procedure

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A12](#a12), [A14](#a14).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Define recovery-point and recovery-time objectives before production cutover; document database, raw snapshots, model artifacts, and release approvals as one recoverable evidence set. Set retention and access controls, back up migration metadata, and rehearse restoration in an isolated environment. Never invent missing provenance during recovery.

**Acceptance criteria:**

- Recovery objectives and retention policy are recorded before drill.
- A restored run resolves every evidence/artifact reference.
- Missing dependencies block publication.

**Quality gate:** Restore into isolation, verify checksums/counts and record achieved data loss/time against the agreed objectives; no drill means gate not passed.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="m02"></a>
### M02 — P1 — Unify evaluation and model artifact approval

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M01](#m01), [A06](#a06), [A10](#a10), [A14](#a14), [A16](#a16), [R01](#r01).

**Source:** [review context](../docs/frontend-backend-migration.md).

**Scope and deliverables:** Use shared training/inference in evaluation and a model registry containing complete input identities. Bind approval to an immutable evidence report and reject approvals for changed specifications.

**Acceptance criteria:**

- Model/quote/data/config references resolve.
- Cache misses occur on any identity change.
- No unapproved model becomes an eligible recommendation.

**Quality gate:** Approval mismatch and live/replay parity checks pass; R02 supplies empirical evidence separately.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="m03"></a>
### M03 — P1 — Build authenticated API, durable workers and storage

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M01](#m01), [A07](#a07), [A12](#a12), [IMP01](#imp01), [IMP02](#imp02), [IMP04](#imp04), [IMP07](#imp07).

**Source:** [review context](../docs/frontend-backend-migration.md).

**Scope and deliverables:** Implement the proposed FastAPI/OpenAPI interfaces, Supabase Auth verification, least-privilege PostgreSQL access, immutable artifact storage, transactional job/outbox creation, Celery/Redis delivery and idempotent worker completion. Separate interactive reads from CPU-heavy jobs.

**Acceptance criteria:**

- Unauthorized access fails.
- Identical idempotency requests resolve to one logical run and conflicting bodies return conflict.
- Queued/running/terminal state survives restart.
- Failed authoritative commits do not publish results.

**Quality gate:** Isolated real-service contract, concurrency, duplicate-delivery, process-restart and database-failure tests pass; no client secrets or CPU fitting in HTTP handlers.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="m04"></a>
### M04 — P1 — Deliver the separate production web frontend

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M03](#m03), [UX01](#ux01), [UX02](#ux02), [UX03](#ux03), [UX04](#ux04), [UX05](#ux05), [UX06](#ux06), [UX07](#ux07), [UX08](#ux08), [UX09](#ux09), [UX10](#ux10), [UX11](#ux11), [UX12](#ux12).

**Source:** [review context](../docs/frontend-backend-migration.md).

**Scope and deliverables:** Build Next.js/TypeScript Matches, Performance, History and authorized advanced/settings views using generated API types. Keep computations and provider secrets server-side. Implement real available/loading/stale/failed/empty states, filters, saved-run context and deliberate result entry.

**Acceptance criteria:**

- All user-facing values come from explicit API results.
- No sample-data recovery or browser-side probability/EV calculations exist.
- Authentication and role boundaries apply to navigation and requests.

**Quality gate:** Component and browser/API journey checks pass on valid and failure responses; QA01/QA02 provide separate release-level accessibility and user verification.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="m05"></a>
### M05 — P1 — Complete shadow validation and operational readiness

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M02](#m02), [M03](#m03), [M04](#m04), [R02](#r02), [IMP05](#imp05), [IMP09](#imp09), [QA01](#qa01), [QA02](#qa02).

**Source:** [review context](../docs/frontend-backend-migration.md).

**Scope and deliverables:** Run the new system on traceable real snapshots without production recommendation publication. Compare old/new results, classifying expected changes from corrected math. Verify quotas, recovery, monitoring, access controls and latency under representative load.

**Acceptance criteria:**

- All material output differences are explained.
- Each releasable league/version has appropriate evidence.
- Restore and monitoring checks meet recorded objectives.
- Unresolved P0/P1 release blockers are listed.

**Quality gate:** Signed readiness evidence links run comparisons, gate report, restore drill and UX tests; a negative model gate blocks recommendation release even if the software is healthy.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="m06"></a>
### M06 — P1 — Perform controlled cutover and verify rollback

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M05](#m05).

**Source:** [review context](../docs/frontend-backend-migration.md).

**Scope and deliverables:** Prepare a reversible application deployment and compatible database migration. Freeze legacy writes, preserve legacy records with unknown provenance, switch to the authoritative API, and retain Streamlit only as an internal read-only diagnostic client. Document rollback and recommendation-disable procedures.

**Acceptance criteria:**

- There is one writer path.
- Legacy data counts reconcile without invented positions/provenance.
- Rollback restores a compatible verified build and never reactivates demo/fallback recommendations.

**Quality gate:** Rehearse cutover/rollback in staging, then record deployment authorization separately; this documentation task grants no permission to deploy.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="qa01"></a>
### QA01 — P1 — Verify mobile and accessibility behavior

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M04](#m04).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Test the actual rendered core journeys against WCAG 2.2 AA targets: keyboard and focus, screen-reader status messages, contrast, labels, chart alternatives, touch targets, zoom/reflow at 320 CSS pixels, long names, and reduced motion. Record browsers, devices and assistive technology.

**Acceptance criteria:**

- Core actions work without pointer or color-only interpretation.
- Content/actions remain reachable at narrow width and zoom.
- No unresolved critical/serious accessibility defect remains in core flows.

**Quality gate:** Combine automated checks with documented manual keyboard/screen-reader/mobile tests; source inspection alone cannot pass this gate.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="qa02"></a>
### QA02 — P1 — Validate complete user journeys with representative users

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M04](#m04).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Run first-visit discovery, source unavailability, changed settings, interrupted update, history navigation, filtered export and result correction scenarios. Predefine task scripts and observable success criteria; use representative users unfamiliar with internal model terminology.

**Acceptance criteria:**

- Participants can locate a match without credentials/model setup, identify which run they see, recover from failure and deliberately save/correct a result.
- All blocking misunderstandings are resolved and retested.

**Quality gate:** Attach anonymized task observations, completion outcomes and remaining severity-ranked issues; do not invent participant counts or results. Record sample and exit threshold before sessions.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="r01"></a>
### R01 — P1 — Select per-league decay with chronological validation

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A10](#a10), [A14](#a14), [A16](#a16), [IMP10](#imp10).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Implement the configured xi grid sweep using only training/validation windows. Save every trial, source policy, cutoff, exclusion count, score and chosen parameter. Freeze the winning per-league configuration before final holdout evaluation.

**Acceptance criteria:**

- Identical snapshots reproduce trial order and selected xi.
- No final-holdout outcome influences selection.
- A league lacking sufficient validation evidence has no approved selected model.

**Quality gate:** Chronology tests and reproducible trial manifest pass; review selection rationale before R02.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="r02"></a>
### R02 — P1 — Run and archive the real historical evidence pack

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A01](#a01), [A02](#a02), [A03](#a03), [A04](#a04), [A05](#a05), [A06](#a06), [A08](#a08), [A09](#a09), [A10](#a10), [A14](#a14), [A16](#a16), [R01](#r01).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Run corrected walk-forward evaluation over the declared real historical corpus for all five leagues. Save a versioned report, machine-readable metrics, input/config/build identities, exclusions, and per-league gate decisions. Intended initial report location: docs/reports/gate_decision_initial.md; do not create fabricated results to fill it.

**Acceptance criteria:**

- Every requested league has evidence or an explicit insufficient-data result.
- Report includes baseline contrasts, three-way scores, calibration and sample coverage.
- Inconclusive/negative results are retained honestly.

**Quality gate:** Reproduce the report from immutable snapshots and review its numeric decisions; completing this task does not require a positive gate or authorize deployment.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux01"></a>
### UX01 — P1 — Setup overwhelms first-time users

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Make the main journey competition/date selection followed by available forecasts. Move ingestion, credentials, model controls, and storage configuration into an operator-only Settings area. Use “View forecasts” for viewing existing results and “Update analysis” for a new calculation; do not imply these are the same operation. Keep synthetic data outside the production journey.

**Acceptance criteria:**

- A first-time user finds a match without model settings or credentials.
- Only authorized operators see technical setup.
- Unavailable real data has a next step.

**Quality gate:** Prototype/browser task walkthrough passes the stated first-run scenario; synthetic output is never used to complete it.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux02"></a>
### UX02 — P1 — Changed controls can appear beside old results

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A14](#a14).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Bind every result to an immutable run and show “Results from…” with timestamp and an input summary. Distinguish display-only filters from analysis settings. After analysis settings change, show “Settings changed — update analysis” and label the prior results explicitly. Preserve old results as history, not as apparently current output.

**Acceptance criteria:**

- Changed analysis settings mark prior results as old.
- Display-only filters do not imply refitting.
- Page navigation preserves selected run identity.

**Quality gate:** Test edit, failed update, successful update, refresh and back navigation; screenshots show accurate run/time/input labels.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux03"></a>
### UX03 — P1 — Status wording overstates readiness

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A08](#a08), [A12](#a12).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Use Not checked, Checking, Available, and Unavailable states with actual last-success timestamps and coverage. Distinguish “Credentials configured” from “Data retrieved.” Surface run-specific save status near the result; keep cumulative technical error counts in operator diagnostics.

**Acceptance criteria:**

- Unchecked services show no success state.
- Actual last-success time and coverage are visible.
- Save failure is distinct from completed analysis.

**Quality gate:** Exercise unchecked, checking, available, partial, unavailable and persistence-failed UI states against controlled API responses.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux04"></a>
### UX04 — P1 — Forecast confidence and recommendation wording conflict

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A04](#a04), [A06](#a06).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Show numeric probabilities, data origin, and eligibility status. Remove qualitative confidence labels unless supported by validated thresholds. Keep research output free of production recommendation language. Any future eligible recommendation needs the backend evidence gate described in audit A06; changing a badge cannot establish eligibility.

**Acceptance criteria:**

- Research/demo/stale/failed output carries no eligible recommendation.
- Low probabilities receive no unsupported moderate badge.
- Status is not conveyed only by color.

**Quality gate:** Inspect all card/expanded/export states with approved and blocked fixtures; each label matches backend eligibility.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux05"></a>
### UX05 — P1 — Long-running work lacks useful progress and recovery

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M03](#m03).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Show completed/current stages, elapsed time, and an explicit terminal state. Provide Retry for the failed operation while preserving inputs. Add cancellation where the worker can safely support it; do not advertise a cancel action that does not stop work. Use progress counts when available and avoid invented completion percentages or time estimates. Recovery must not substitute sample data or another model.

**Acceptance criteria:**

- Progress uses real stages/counts.
- Cancellation is truthful.
- Retry preserves inputs and avoids duplicate records.
- Refresh recovers job status.

**Quality gate:** Interrupt/retry/cancel/navigate during durable jobs and verify the terminal UI state matches stored job state.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux06"></a>
### UX06 — P1 — Result recording invites accidental or ambiguous submissions

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A11](#a11).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Identify matches by full team names, competition, and date. Require deliberate score entry, explain optional closing odds in plain language, and preview the selected match/result before submission. Retain a persistent confirmation and offer an audited correction action. Apply audit A11's prediction-versus-position distinction to all financial fields.

**Acceptance criteria:**

- An untouched score form cannot submit a draw.
- Readable fixture details persist in confirmation.
- Correction preserves the original result event.

**Quality gate:** Browser tests cover untouched, valid, invalid, duplicate and corrected submission plus refresh after save.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

## P2 tasks
<a id="a16"></a>
### A16 — P2 — Drawdown and evidence sufficiency can mislead

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A20](#a20).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Include initial equity, declare settlement ordering or aggregate daily returns, check finite valid prices and intervals, require independent-block sufficiency, and expose failed calibration as unavailable. Test the one-loss case, one-block case, NaN inputs, and optimizer failure. Use joint blocks for temporal comparative statistics.

**Acceptance criteria:**

- A single one-unit loss reports minus one drawdown.
- Insufficient independent weeks or nonfinite inputs yield unavailable evidence.
- Failed calibration is not reported as valid.

**Quality gate:** Hand-calculated metric cases, one-block bootstrap, malformed intervals and forced calibration failure tests pass.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a17"></a>
### A17 — P2 — UI confidence labels and displayed evidence overstate readiness

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [UX04](#ux04), [UX09](#ux09), [UX10](#ux10), [UX12](#ux12).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Show numeric probabilities with source/run/fit status and the probability of the most-likely score. Remove unsupported qualitative ratings or validate their thresholds on held-out data. Bind results to immutable input/run IDs. Distinguish forecasts, model-estimated price comparisons, and separately approved recommendations in API and UI. This is a product-consistency finding, not a legal compliance assessment.

**Acceptance criteria:**

- Confidence/status wording matches model evidence.
- Current output identifies its source/run.
- Explanatory notices and exported terminology agree.

**Quality gate:** Audit every page and export against an approved wording/state checklist; attach rendered evidence and links to source metrics.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a18"></a>
### A18 — P2 — Shrinkage and optional xG fitting need separate validation

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A01](#a01), [A03](#a03), [A14](#a14).

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Test reference-choice sensitivity and choose a mathematically consistent centered penalty. Treat xG as a separate model specification with its own likelihood, version, and held-out evidence; do not substitute it into the goal-count model under the same approval. These are modeling-design risks, not measured accuracy claims from this audit.

**Acceptance criteria:**

- Chosen shrinkage is consistent with the parameter basis.
- Reference-choice sensitivity is measured.
- XG models have separate definitions and approval identities.

**Quality gate:** Record controlled reference/target experiments and their held-out results; unvalidated xG variants remain research-only regardless of apparent improvement.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a19"></a>
### A19 — P2 — Documentation and verification labels contradict the implementation

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Treat these earlier descriptions as historical intent. This dated audit and the proposed migration document supersede conflicting readiness claims. Replace VERIFIED labels with reproducible evidence references when implementation checks exist. Do not mark this audit's findings fixed merely because documentation has been added.

**Acceptance criteria:**

- Every guarantee states implemented versus proposed status.
- Equations agree with tested code.
- All findings have IDs and evidence-linked closure status.

**Quality gate:** Documentation cross-check resolves defence sign, rho bounds, Kelly terminology, ledger semantics and fallback claims without claiming unrun tests passed.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="a20"></a>
### A20 — P2 — Runtime assurance and service boundaries are missing

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/audit-2026-09-20.md).

**Scope and deliverables:** Follow the [frontend/backend migration proposal](frontend-backend-migration.md). Pin a reproducible environment in implementation work, add invariant and contract checks, and run integration tests against isolated services before rollout. No dependencies were installed during this read-only code audit.

**Acceptance criteria:**

- A clean isolated environment installs reproducibly.
- Numerical/domain tests run without production credentials.
- Core services do not import UI runtime code.

**Quality gate:** Capture clean-environment CI output and dependency/build identity; required failing tests cannot be skipped to declare completion.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="f01"></a>
### F01 — P2 — Report COVID-era and season-specific robustness

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A10](#a10), [A14](#a14), [A16](#a16), [R02](#r02).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Add explicit, sourced season/period labels for affected matches and report model/market metrics by period and league. Investigate adjustment only as a separate recorded experiment; do not hard-code an unsupported correction factor.

**Acceptance criteria:**

- All period definitions have provenance.
- Overall and subgroup counts reconcile.
- Small cohorts stay inconclusive.
- Any adjustment is versioned and tested out of sample.

**Quality gate:** Reproduce stratified reports and compare adjusted/unadjusted variants on fixed cohorts before adopting a change.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp06"></a>
### IMP06 — P2 — Align development and deployment protection settings

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Keep protection enabled in ordinary development and smoke-test supported launch modes. If a debugging exception is necessary, make it explicit, temporary, and limited to a private environment; do not publish it as the default launcher. Address origin/proxy configuration rather than routinely disabling protection. Review non-root runtime execution and writable-directory limits during deployment hardening.

**Acceptance criteria:**

- Supported launch modes retain intended XSRF/origin protections.
- Forwarded-port visibility is verified separately.
- Runtime privileges/writable paths are documented.

**Quality gate:** Smoke-test each supported launch mode with protection enabled and record effective settings without changing production access during testing.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp07"></a>
### IMP07 — P2 — Give CLI automation explicit outcomes and exit codes

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [IMP04](#imp04).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Define typed outcomes such as completed, empty_schedule, unavailable_input, invalid_configuration, and persistence_failed. Emit a concise structured summary and appropriate exit status. Share run validation and outcome semantics with the future API. Expose explicit configuration selection rather than requiring callers to reach into Python internals.

**Acceptance criteria:**

- Unavailable inputs and failed writes return failure status.
- Verified empty schedules are explicitly distinct.
- Structured run summaries are stable.

**Quality gate:** Subprocess contract tests cover invalid configuration, missing source, empty schedule, successful run and persistence failure.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp08"></a>
### IMP08 — P2 — Make provider usage quota-aware

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M03](#m03).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Centralize provider requests in workers, record supplied quota/reset metadata, deduplicate equivalent fetches, and apply bounded same-provider retries with jitter and provider retry guidance. Separate “no events,” “quota exhausted,” and “provider unavailable.” Set polling frequency using actual account entitlement and required freshness, not the README's unverified free-tier numbers.

**Acceptance criteria:**

- Equivalent fetches share one logical request.
- Quota state affects retry scheduling.
- Rate limiting never selects a replacement source or fabricated data.

**Quality gate:** Simulated shared-budget/concurrency/429 tests prove bounded retries and correct freshness/coverage reporting.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp09"></a>
### IMP09 — P2 — Monitor model quality after approval

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A06](#a06), [A16](#a16), [M03](#m03).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Track observed coverage, unknown teams, fit failures, prediction distributions, delayed settlement coverage, calibration, and market-relative scores by league and season. Separate data problems from outcome-quality changes. Use only sufficiently settled cohorts and predefined policies; do not automatically retune a model on the same period being used to judge it. Suspend recommendations on policy breach rather than substituting a backup model.

**Acceptance criteria:**

- Settled cohorts and independent-sample rules govern quality checks.
- Stable state produces no duplicate review event.
- Breach suspends eligibility without model substitution.

**Quality gate:** Replay controlled drift, coverage loss, delayed settlement and stable scenarios against the documented policy; record review-event evidence.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp10"></a>
### IMP10 — P2 — Add reproducible experiment comparison and ablation reports

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A10](#a10), [A14](#a14), [A16](#a16).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Record a controlled experiment matrix: simple baseline, corrected Dixon–Coles, candidate decay policies, and separately specified xG models. Compare on identical eligible fixtures, source policies, and chronological splits. Report exclusions and paired/block uncertainty with each metric; freeze a final confirmation period. Track every trial, including unsuccessful ones, to avoid selective reporting.

**Acceptance criteria:**

- Trials use saved data/config/splits and identical comparison cohorts.
- Unsuccessful trials are retained.
- Final holdout is not reused for tuning.

**Quality gate:** Reproduce an experiment report and trial registry; verify cohort equality and detect holdout leakage before any promotion.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux07"></a>
### UX07 — P2 — Filters and summary totals behave inconsistently

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Adopt explicit All competitions and Clear selection actions; an empty selection shows no rows everywhere. Make page summary totals follow active filters. If global totals are useful, label them “All records” separately. Export exactly the filtered dataset and show the exported record count.

**Acceptance criteria:**

- Empty selection means no rows on every page.
- Summary counts match labelled scope.
- Export includes exactly the filtered result set.

**Quality gate:** Use the same filter/reset/export test dataset on all screens and reconcile displayed counts with downloaded rows.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux08"></a>
### UX08 — P2 — Match browsing lacks basic discovery controls

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A08](#a08).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Add team search, competition/date filters, “Showing X of Y,” and Load more or pagination. Show local kickoff time and timezone only where the source provides them; otherwise say “Kickoff time unavailable.” Keep all results reachable without requiring a raw-data table.

**Acceptance criteria:**

- Search finds a fixture beyond the first ten cards.
- Counts/pagination reflect filters.
- Unknown kickoff time is explicit rather than invented.

**Quality gate:** Test large fixture sets, no matches, long names, multiple dates and known/unknown timezone precision.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux09"></a>
### UX09 — P2 — Probability presentation is unbalanced

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A05](#a05).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Display the headline score's probability and clarify that “most likely” does not mean likely in absolute terms. Use a labelled Home/Draw/Away probability distribution with equivalent text. Show expected goals as a model estimate, explained on demand. Keep percentage rounding consistent and avoid apparent contradictory totals.

**Acceptance criteria:**

- Headline score includes its probability.
- All three outcomes have labels/text alternatives.
- Rounding cannot suggest contradictory totals.

**Quality gate:** Compare rendered values to the result payload for ordinary/extreme probabilities; verify no single-outcome progress bar implies total confidence.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux10"></a>
### UX10 — P2 — Diagnostics explanations can reverse interpretation

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A18](#a18).

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Correct the chart caption and sparse-team explanation. Use “Attack strength” and “Defensive weakness — lower is better,” readable team names, and plain-language interpretation before Greek symbols. Put mathematical details in an expandable technical section.

**Acceptance criteria:**

- Chart captions agree with axis direction.
- Sparse-team language matches shrinkage.
- Names are readable and technical detail is optional.

**Quality gate:** A fixture with known ratings renders interpretable axes, labels and descriptions; manually verify the strong-attack/weak-defence quadrant.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux11"></a>
### UX11 — P2 — Navigation and empty states create dead ends

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Provide contextual navigation such as “Open Matches,” “Choose saved analysis,” or an operator-only “Review data connection.” Distinguish no saved analysis, no matching fixtures, no data coverage, and provider failure. Preserve the user's selected run and filters when returning from detail screens.

**Acceptance criteria:**

- Every empty state identifies its cause.
- Contextual actions reach the right screen.
- Returning from details restores the selected context.

**Quality gate:** Walk no-history, no-fixtures, no-filter-matches and provider-failure journeys without synthetic recovery.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="ux12"></a>
### UX12 — P2 — Terminology and information hierarchy vary across pages

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** None.

**Source:** [review context](../docs/ui-ux-audit-2026-09-20.md).

**Scope and deliverables:** Use “draw” consistently. Organize user navigation around Matches, Performance, and History; expose Advanced diagnostics and Settings separately. Explain xG, EV, and CLV where encountered, and separate user-facing availability/save status from technical service diagnostics. Do not imply a predicted stake is an executed bet.

**Acceptance criteria:**

- Use consistent draw and task-based navigation terminology.
- Explain advanced metrics at use.
- Distinguish estimates, recommendations and executed positions.

**Quality gate:** Review page labels, captions, tables and exports against one glossary; representative users can explain core labels in QA02.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

## P3 tasks
<a id="f02"></a>
### F02 — P3 — Evaluate bounded cross-league parallel execution

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [M03](#m03), [IMP12](#imp12).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Benchmark sequential and bounded worker execution over identical league jobs. Define concurrency/memory limits, isolate artifacts, and support failure/cancellation per league without silently skipping requested work.

**Acceptance criteria:**

- Parallel and sequential outputs match within declared numerical tolerance.
- Memory stays within measured limits.
- Failed leagues remain visibly failed and duplicate delivery does not duplicate results.

**Quality gate:** Record speed/memory measurements and artifact integrity under restart/concurrent execution; retain sequential execution if parallelization offers no justified benefit.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="f03"></a>
### F03 — P3 — Assess Betfair exchange data and liquidity integration

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A09](#a09), [M03](#m03), [IMP08](#imp08).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Research official API access, provider terms, event/market identity, quote timing, spread, commission and liquidity. Produce an explicit go/no-go integration decision. Only if subsequently approved, implement an isolated adapter with clear contracts; never place bets as part of this task.

**Acceptance criteria:**

- Feasibility report identifies access/entitlement requirements and supported data.
- Unavailable credentials produce a blocked integration decision rather than mocked production quotes.
- Costs/limits are verified at decision time.

**Quality gate:** Review sourced feasibility and representative authorized read-only contract evidence before any adapter rollout; synthetic tests remain isolated and no financial transaction is executed.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="f04"></a>
### F04 — P3 — Design opt-in notification delivery

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A06](#a06), [M03](#m03), [IMP09](#imp09).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Design Telegram/Discord or other chosen channels with explicit opt-in destination, unsubscribe, deduplication, retry limits and delivery audit. Distinguish research status events from approved recommendation alerts. Obtain separate authorization for sending or connecting accounts.

**Acceptance criteria:**

- Repeated processing sends at most one logical event per version/destination.
- Blocked/stale/withdrawn recommendations cannot generate actionable alerts.
- Opt-out stops future deliveries and secrets are redacted.

**Quality gate:** Use a mock transport for ordinary tests; test real delivery only to an explicitly authorized test destination. This backlog item does not authorize messages now.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp11"></a>
### IMP11 — P3 — Add forecast explanations grounded in actual model output

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A14](#a14), [UX09](#ux09).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Add “About this forecast” with training period, competition/model identity, actual sample coverage, expected-goal estimates, home-advantage contribution, and probability changes between saved runs. Explain uncertainty and missing information. Use deterministic templates tied to recorded values; do not invent injury, lineup, weather, or tactical explanations absent from the data. A difference between model runs is descriptive, not proof of a causal factor.

**Acceptance criteria:**

- Every explanation is traceable to a recorded value/calculation.
- Missing features are explicit.
- No invented injuries/tactics/weather or causal claims appear.

**Quality gate:** Deterministic explanation cases link each claim to data/model fields; identical inputs produce consistent content.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.

<a id="imp12"></a>
### IMP12 — P3 — Optimize only against measured workload and quality

**Status:** Open — not implemented. **Owner/reviewer:** unassigned; assign before implementation. **Dependencies:** [A01](#a01), [A05](#a05), [A14](#a14).

**Source:** [review context](../docs/improvements-2026-09-20.md).

**Scope and deliverables:** Benchmark ingestion, fitting, inference, persistence, and full-corpus evaluation separately after mathematical corrections. Measure cold and warm runs, memory, concurrency, and source-call counts. Start with transactional batch persistence and reuse of validated immutable artifacts. Evaluate optimizer improvements and vectorized operations using numerical parity tests; increase worker parallelism only within measured resource limits. Defer GPU/autodiff rewrites until evidence justifies them.

**Acceptance criteria:**

- Benchmark data/config/hardware are fixed.
- Before/after time and memory are recorded.
- Output parity and no-substitution rules remain intact.

**Quality gate:** Attach workload measurements and numerical parity results; a speed claim without a baseline or changed eligibility policy fails the gate.

**Closure evidence:** Attach task-specific test/review artifacts plus the shared closure record. **Gate result:** Not run.


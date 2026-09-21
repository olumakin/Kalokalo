# Project improvements and enhancement review

Date: 2026-09-20. Source baseline: `d912bc27c074d1e8d52e45f7cccbaef0b8c53ef6`. Status: recommendations only; all items open.

## Summary and scope

Preserve the Python analytical core after correcting its mathematics and data boundaries. Prioritize trustworthy outputs, secure operation, reproducibility, and recovery before expanding features. The proposed separate frontend/API/worker architecture remains suitable; do not replace it with a larger collection of microservices without measured need.

This follow-up checked deployment/build files, ingestion, configuration, CLI behavior, risk helpers, tests, and the existing review documents. It adds six concrete defect/risk findings and six enhancement or operational-readiness proposals. Some refine existing broad recommendations; related audit IDs below prevent treating them as unrelated duplicates. No code, tests, data, schema, dependencies, or deployment settings were changed. No secrets or production logs were inspected, and no providers or databases were contacted.

Existing reviews remain authoritative for their scope:

- [A01–A20: engine, data, ledger, and architecture audit](audit-2026-09-20.md).
- [UX01–UX12: UI/UX audit](ui-ux-audit-2026-09-20.md).
- [Frontend/backend architecture proposal](frontend-backend-migration.md).
- [Combined backlog](../tasks/todo.md).

**Priority:** existing P0 engine release blockers come first. P1 items below are needed before trustworthy production operation; P2 items improve reliable automation and maintainability; P3 enhancements follow correctness and evidence. A proposed capability is not a claim of an observed production incident. Effort labels are relative scope estimates, not delivery commitments.

## P1 — Production integrity and recovery

### IMP01 — Exclude local secrets from image builds

**Type:** concrete conditional security risk. **Effort:** small. **Related:** A20.

**Evidence:** `Dockerfile` uses `COPY . .`. `.dockerignore` excludes caches and tests but does not exclude `.streamlit/secrets.toml` or `.env` files. `.gitignore` excludes Streamlit secrets, which does not exclude them from a local Docker build context. Docker documents `.dockerignore` as the build-context exclusion mechanism: [Docker build context](https://docs.docker.com/build/concepts/context/).

**Impact:** If local credentials exist when an image is built from the working directory, they can be copied into image layers and distributed with that image. This review did not inspect credentials or images and does not establish that any secret has already leaked.

**Recommendation:** Explicitly exclude secret files, private keys, and local artifacts; preferably copy an allowlisted runtime tree. Supply runtime secrets through the deployment environment, and use build-secret mounts only if a build genuinely needs credentials. Keep production images free of test/demo observations.

**Acceptance:** Build an isolated test image with harmless sentinel secret files and verify they appear in neither the final filesystem nor distributable layers. Inspect the resulting build context without accessing real credentials. Confirm deployment still receives required secrets at runtime.

### IMP02 — Redact provider credentials from failure logs

**Type:** concrete conditional security risk. **Effort:** small. **Related:** A20 and migration logging policy.

**Evidence:** `src/ingestion/odds_feed.py:105–114` sends `apiKey` as a query parameter, calls `raise_for_status()`, then logs the entire exception. Requests' implementation includes the response URL in HTTP error messages: [Requests source](https://requests.readthedocs.io/en/latest/_modules/requests/models/).

**Impact:** A failed HTTP request can write the API key into logs through its URL. This is a source-based exposure path, not confirmation that deployed logs contain keys.

**Recommendation:** Log structured provider/league/status/reason fields rather than raw exceptions or URLs. Redact sensitive query parameters and headers in all adapters, error reporting, and UI diagnostics. Preserve a non-sensitive correlation ID for support.

**Acceptance:** Simulate authentication failure, rate limiting, server errors, and transport failures using a harmless sentinel key; assert the key appears nowhere in captured logs, surfaced errors, or reports. Retain useful error category and request correlation without credentials.

### IMP03 — Validate downloads before committing the historical cache

**Type:** concrete reliability defect. **Effort:** medium. **Related:** A08 and A13; distinct from stale-cache expiry.

**Evidence:** `src/ingestion/historical.py:64–75` writes response bytes directly to the cache before parsing. The existing-cache read is outside the network/parse exception handler. There is no atomic replacement or payload/schema validation before publishing the cache file.

**Impact:** A malformed HTTP-200 payload or interrupted write can leave a corrupt file. Later runs immediately read that file and may fail repeatedly without attempting a refresh. A syntactically valid CSV with wrong columns can fail farther downstream.

**Recommendation:** Parse and validate a bounded candidate download before accepting it; publish a validated snapshot atomically. Quarantine corrupt cache entries with a reason and retry the same configured source according to policy. Preserve old snapshots for audit, not as silent substitutes for a requested fresh snapshot.

**Acceptance:** HTML returned with status 200, truncated CSV, wrong columns, invalid encoding, and interrupted writes never replace a valid snapshot. A corrupt file yields a controlled unavailable state and can be recovered without manually editing data files. Concurrent readers cannot see partially written data.

### IMP04 — Resolve and validate configuration once

**Type:** concrete configuration/contract gap. **Effort:** medium. **Related:** A13–A15.

**Evidence:** `src/ingestion/historical.py:38–41` returns raw YAML without schema validation. `src/pipeline.py:94–99,152–154` can receive custom settings, but `load_all()` reloads default settings internally for its provider URL. Risk helpers in `src/analytics/edge.py` do not reject negative/nonfinite caps or stake inputs.

**Verification:** Executing the actual dependency-free `apply_risk_caps` function with a negative single-match cap returns a negative stake; passing a negative raw stake also preserves it. The checked-in defaults are positive, so this demonstrates invalid-input behavior, not a claim that default runs already do this.

**Recommendation:** Introduce a typed configuration schema with supported leagues/methods, positive integer limits, finite probabilities/caps, a declared Kelly range, and valid paths/source settings. Resolve defaults and overrides once at the boundary, inject the resolved configuration, and record its hash with each run. Reject invalid settings before downloading, fitting, or writing anything.

**Acceptance:** Invalid numeric values, unsupported methods, and malformed league selections fail clearly. A custom source URL is actually used by ingestion. The exact resolved configuration is reproducible across CLI, UI/API, and worker execution. Keep the configured no-substitution policy immutable for an accepted run.

### IMP05 — Add a documented and tested restore procedure

**Type:** operational-readiness enhancement, not a verified backup outage. **Effort:** medium. **Related:** A12 and migration data-storage design.

**Evidence:** Current documentation proposes authoritative PostgreSQL plus object storage, but contains no completed restore drill or coordinated backup/retention procedure. Local ledger files are excluded from source control; Git is not an audit-data backup. Managed provider backup settings were not inspected.

**Recommendation:** Define recovery-point and recovery-time objectives before production cutover; document database, raw snapshots, model artifacts, and release approvals as one recoverable evidence set. Set retention and access controls, back up migration metadata, and rehearse restoration in an isolated environment. Never invent missing provenance during recovery.

**Acceptance:** Restore a known run and its dataset/model/quote/report references, verify checksums and record counts, and demonstrate that a missing artifact blocks dependent publication. Record achieved recovery time and data loss against agreed objectives. Recommendations stay blocked until restored approvals and dependencies are validated.

## P2 — Reliable automation and maintainable analysis

### IMP06 — Align development and deployment protection settings

**Type:** concrete configuration risk, exposure not verified. **Effort:** small. **Related:** A20.

**Evidence:** `.streamlit/config.toml` enables XSRF protection, but `.devcontainer/devcontainer.json` launches Streamlit with `--server.enableXsrfProtection false` and forwards its port. This is a less protective development launch policy than the main configuration. Actual forwarded-port visibility was not inspected. [Streamlit configuration reference](https://docs.streamlit.io/develop/api-reference/configuration/config.toml) describes this protection.

**Recommendation:** Keep protection enabled in ordinary development and smoke-test supported launch modes. If a debugging exception is necessary, make it explicit, temporary, and limited to a private environment; do not publish it as the default launcher. Address origin/proxy configuration rather than routinely disabling protection. Review non-root runtime execution and writable-directory limits during deployment hardening.

**Acceptance:** Supported launch paths have a documented effective protection configuration. Uploads and authenticated actions work with it enabled. No published devcontainer/start command silently disables protection; verify port access separately rather than assuming forwarding means public exposure.

### IMP07 — Give CLI automation explicit outcomes and exit codes

**Type:** concrete automation gap. **Effort:** small to medium. **Related:** A20 and UX05.

**Evidence:** `src/pipeline.py:152–185` returns an empty DataFrame for unavailable history or fixtures; `main()` prints “No predictions generated.” and returns normally. It does not distinguish a legitimate empty slate from missing required input with a machine-readable result. League input is split on commas without a boundary schema.

**Impact:** A scheduler can interpret an unavailable run as successful process completion, and cannot reliably decide whether to retry or report an empty schedule.

**Recommendation:** Define typed outcomes such as completed, empty_schedule, unavailable_input, invalid_configuration, and persistence_failed. Emit a concise structured summary and appropriate exit status. Share run validation and outcome semantics with the future API. Expose explicit configuration selection rather than requiring callers to reach into Python internals.

**Acceptance:** Missing history and failed required writes yield non-success exits; a verified zero-fixture schedule is explicitly distinguishable and can exit successfully. Whitespace/unknown league codes are handled predictably. Failure runs do not record replacement predictions.

### IMP08 — Make provider usage quota-aware

**Type:** enhancement. **Effort:** medium. **Related:** A08–A09, UX03–UX05.

**Evidence:** `get_upcoming_fixtures()` loops selected leagues and makes provider calls; the UI caches fixtures for 15 minutes. Code does not track remaining request allowance, provider retry timing, or shared usage budgets. This is not evidence that the current account has exhausted quota; no account was inspected.

**Recommendation:** Centralize provider requests in workers, record supplied quota/reset metadata, deduplicate equivalent fetches, and apply bounded same-provider retries with jitter and provider retry guidance. Separate “no events,” “quota exhausted,” and “provider unavailable.” Set polling frequency using actual account entitlement and required freshness, not the README's unverified free-tier numbers.

**Acceptance:** Concurrent equivalent requests share one logical fetch; simulated rate limits schedule bounded retries and never choose another provider or static card. Operators see data age and quota state without API keys. A quota-limited run cannot claim current coverage.

### IMP09 — Monitor model quality after approval

**Type:** enhancement. **Effort:** medium. **Related:** A06, A16 and migration evidence policy.

**Evidence:** The project computes historical metrics but has no implemented post-release drift/quality monitor. A one-time positive gate is not a continuing health signal.

**Recommendation:** Track observed coverage, unknown teams, fit failures, prediction distributions, delayed settlement coverage, calibration, and market-relative scores by league and season. Separate data problems from outcome-quality changes. Use only sufficiently settled cohorts and predefined policies; do not automatically retune a model on the same period being used to judge it. Suspend recommendations on policy breach rather than substituting a backup model.

**Acceptance:** Known deterioration and coverage-loss scenarios create a recorded review event; stable inputs do not create duplicate events. Small/unfinished samples remain inconclusive. Every decision links to the relevant model, cohort, timestamps, and monitoring policy.

### IMP10 — Add reproducible experiment comparison and ablation reports

**Type:** enhancement. **Effort:** medium to large. **Related:** A10, A14, A18 and migration holdout policy.

**Evidence:** Model options include decay, rho correction, and optional xG targets, but no implemented comparison workflow explains which component improves held-out results. Existing gate integration tests use synthetic data and establish software behavior rather than real predictive advantage.

**Recommendation:** Record a controlled experiment matrix: simple baseline, corrected Dixon–Coles, candidate decay policies, and separately specified xG models. Compare on identical eligible fixtures, source policies, and chronological splits. Report exclusions and paired/block uncertainty with each metric; freeze a final confirmation period. Track every trial, including unsuccessful ones, to avoid selective reporting.

**Acceptance:** Another run reproduces the same cohort and report from saved inputs; comparisons cannot quietly change test populations. A model is promoted only by the prespecified evidence policy. More complex models are not presumed better.

## P3 — Product value and measured optimization

### IMP11 — Add forecast explanations grounded in actual model output

**Type:** product enhancement. **Effort:** medium. **Related:** UX09–UX12, A17 and immutable model metadata.

**Evidence:** Current cards show scores/xG/probabilities while diagnostics expose ratings on another screen. There is no compact per-fixture view connecting a forecast to its trained model, data coverage, and actual available inputs.

**Recommendation:** Add “About this forecast” with training period, competition/model identity, actual sample coverage, expected-goal estimates, home-advantage contribution, and probability changes between saved runs. Explain uncertainty and missing information. Use deterministic templates tied to recorded values; do not invent injury, lineup, weather, or tactical explanations absent from the data. A difference between model runs is descriptive, not proof of a causal factor.

**Acceptance:** Every factual explanation links to a stored field or explicitly defined calculation. Two identical runs produce consistent explanations. Missing features are identified as unavailable. Users can distinguish model estimates from observed match statistics and bookmaker prices.

### IMP12 — Optimize only against measured workload and quality

**Type:** performance enhancement. **Effort:** medium to large. **Related:** A20 and API/worker proposal.

**Evidence:** Profile-rho fitting repeatedly invokes an inner optimizer; backtests retrain periodically; local ledger recording rewrites a whole file for each row. These are candidate bottlenecks, but no end-to-end timing or memory benchmark was run in this environment.

**Recommendation:** Benchmark ingestion, fitting, inference, persistence, and full-corpus evaluation separately after mathematical corrections. Measure cold and warm runs, memory, concurrency, and source-call counts. Start with transactional batch persistence and reuse of validated immutable artifacts. Evaluate optimizer improvements and vectorized operations using numerical parity tests; increase worker parallelism only within measured resource limits. Defer GPU/autodiff rewrites until evidence justifies them.

**Acceptance:** Publish before/after runtime and memory results on the same verified dataset/configuration/hardware, with matching eligible outputs and declared numerical tolerance. Performance changes cannot alter source policy, training cutoffs, probability validity, or approval semantics to make runs appear faster.

## Recommended sequence

| Order | Work | Completion evidence |
|---|---|---|
| 1 | Existing A01–A05 plus IMP01–IMP04 | Correct probability/identity behavior, no synthetic substitutions, safe secrets/cache/configuration boundaries |
| 2 | Existing evidence and ledger fixes plus IMP05–IMP07 | Real-data gate, authoritative records, restore drill, safe launch and machine-readable failure outcomes |
| 3 | Frontend/backend migration, UX01–UX12, IMP08–IMP10 | Honest UI state, durable workers, provider budgets, repeatable monitoring/experiments |
| 4 | IMP11–IMP12 | Traceable explanations and measured performance improvements |

Do not add more leagues, new scrapers, automated betting, or notification channels merely to expand feature count before this foundation is verified. Existing Betfair/FBref ideas remain optional future research. Notifications and deployment actions are not authorized or performed by this review.

## Verification limits and documentation controls

Completed: source inspection, comparison with existing audits, AST syntax parsing of 50 Python files, and a dependency-free probe of the actual risk-cap helper with invalid negative inputs. Consulted official Docker, Requests, and Streamlit references for the specific platform behaviors cited above.

Not completed: full pytest, live model fits, image builds, production-log inspection, secret scanning of private credentials, provider usage checks, real restore drills, benchmarks, or runtime UI testing. Missing local scientific/test dependencies from the prior audit were not installed. No proposal is marked implemented or empirically successful.

Non-Markdown tracked-file fingerprint before documentation edits: `938f69b666eaaf9cb4780a442392e9448ec8bc5a13903563fefb29f989695052`. It is SHA256 over a sorted JSON mapping of tracked non-Markdown paths to their content SHA256 values. Documentation additions preserve the previous A/UX identifiers and open statuses.

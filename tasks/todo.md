# Prioritized project task backlog

Updated: 2026-09-20. **58 open task specifications; no findings implemented.** Every row links to detailed scope, dependencies, acceptance criteria, quality gate and evidence requirements in the [detailed backlog](detailed-backlog.md).

The [review index](../docs/README.md) retains original evidence. [Historical milestones](historical-milestones.md) preserve prior development claims without marking current audit work complete. Overlapping tasks share evidence but keep their distinct acceptance checks.

Coverage: A01–A20 (20), UX01–UX12 (12), IMP01–IMP12 (12), M01–M06 (6), R01–R02 (2), QA01–QA02 (2), F01–F04 (4). Environment hardening maps to A20; xi sweep to R01; full-corpus report to R02; accessibility/journey checks to QA01/QA02; all original future items map to F01–F04.

Execution guidance: establish A20/IMP04 test and configuration prerequisites; address P0 and integrity repairs; validate real evidence and authoritative storage; build API/workers and frontend; pass QA and shadow gates before authorized cutover. P3 feature work follows the verified foundation.
## P0

| Status | Task | Dependencies |
|---|---|---|
| Complete | [A01 — Dixon–Coles low-score factors are reversed](detailed-backlog.md#a01) | A20 |
| Complete | [A02 — Synthetic and static observations can produce ordinary recommendations](detailed-backlog.md#a02) | A20 |
| Complete | [A03 — Cross-league team codes merge distinct clubs](detailed-backlog.md#a03) | A20 |
| Complete | [A04 — Failed, substituted, or unknown-team models remain eligible](detailed-backlog.md#a04) | A01,A03 |
| Complete | [A05 — Invalid probability models are repaired by clipping](detailed-backlog.md#a05) | A01 |
| Complete | [M01 — Establish a shared strict domain engine](detailed-backlog.md#m01) | A01,A02,A03,A04,A05,A13,IMP04 |
## P1

| Status | Task | Dependencies |
|---|---|---|
| Complete | [A06 — Gate can approve a selection worse than baseline](detailed-backlog.md#a06) | A10,A14,A16 |
| Complete | [A07 — Audit records are forgeable under the shipped anonymous policies](detailed-backlog.md#a07) | A20 |
| Complete | [A08 — History can stay stale indefinitely; upcoming status is not enforced](detailed-backlog.md#a08) | A13,IMP03 |
| Open | [A09 — Consensus odds are treated as an executable price](detailed-backlog.md#a09) | A13 |
| Complete | [A10 — Historical ingestion does not assemble the gate's required data](detailed-backlog.md#a10) | A13,A09 |
| Complete | [A11 — Settlement accounting conflates predictions with positions](detailed-backlog.md#a11) | A12 |
| Complete | [A12 — Local/remote ledgers diverge and cannot ensure durable publication](detailed-backlog.md#a12) | A07,A14 |
| Complete | [A13 — Input and date validation is weaker than documentation claims](detailed-backlog.md#a13) | A20 |
| Open | [A14 — Reproducibility and training semantics drift between paths](detailed-backlog.md#a14) | A03,IMP04 |
| Complete | [A15 — Exposure limits apply only inside one run](detailed-backlog.md#a15) | A11,IMP04 |
| Complete | [IMP01 — Exclude local secrets from image builds](detailed-backlog.md#imp01) | None |
| Complete | [IMP02 — Redact provider credentials from failure logs](detailed-backlog.md#imp02) | None |
| Complete | [IMP03 — Validate downloads before committing the historical cache](detailed-backlog.md#imp03) | None |
| Complete | [IMP04 — Resolve and validate configuration once](detailed-backlog.md#imp04) | None |
| Open | [IMP05 — Add a documented and tested restore procedure](detailed-backlog.md#imp05) | A12,A14 |
| Open | [M02 — Unify evaluation and model artifact approval](detailed-backlog.md#m02) | M01,A06,A10,A14,A16,R01 |
| Complete | [M03 — Build authenticated API, durable workers and storage](detailed-backlog.md#m03) | M01,A07,A12,IMP01,IMP02,IMP04,IMP07 |
| Open | [M04 — Deliver the separate production web frontend](detailed-backlog.md#m04) | M03,UX01,UX02,UX03,UX04,UX05,UX06,UX07,UX08,UX09,UX10,UX11,UX12 |
| Open | [M05 — Complete shadow validation and operational readiness](detailed-backlog.md#m05) | M02,M03,M04,R02,IMP05,IMP09,QA01,QA02 |
| Open | [M06 — Perform controlled cutover and verify rollback](detailed-backlog.md#m06) | M05 |
| Open | [QA01 — Verify mobile and accessibility behavior](detailed-backlog.md#qa01) | M04 |
| Open | [QA02 — Validate complete user journeys with representative users](detailed-backlog.md#qa02) | M04 |
| Open | [R01 — Select per-league decay with chronological validation](detailed-backlog.md#r01) | A10,A14,A16,IMP10 |
| Open | [R02 — Run and archive the real historical evidence pack](detailed-backlog.md#r02) | A01,A02,A03,A04,A05,A06,A08,A09,A10,A14,A16,R01 |
| Open | [UX01 — Setup overwhelms first-time users](detailed-backlog.md#ux01) | None |
| Open | [UX02 — Changed controls can appear beside old results](detailed-backlog.md#ux02) | A14 |
| Open | [UX03 — Status wording overstates readiness](detailed-backlog.md#ux03) | A08,A12 |
| Open | [UX04 — Forecast confidence and recommendation wording conflict](detailed-backlog.md#ux04) | A04,A06 |
| Open | [UX05 — Long-running work lacks useful progress and recovery](detailed-backlog.md#ux05) | M03 |
| Open | [UX06 — Result recording invites accidental or ambiguous submissions](detailed-backlog.md#ux06) | A11 |
## P2

| Status | Task | Dependencies |
|---|---|---|
| Open | [A16 — Drawdown and evidence sufficiency can mislead](detailed-backlog.md#a16) | A20 |
| Open | [A17 — UI confidence labels and displayed evidence overstate readiness](detailed-backlog.md#a17) | UX04,UX09,UX10,UX12 |
| Open | [A18 — Shrinkage and optional xG fitting need separate validation](detailed-backlog.md#a18) | A01,A03,A14 |
| Open | [A19 — Documentation and verification labels contradict the implementation](detailed-backlog.md#a19) | None |
| Open | [A20 — Runtime assurance and service boundaries are missing](detailed-backlog.md#a20) | None |
| Open | [F01 — Report COVID-era and season-specific robustness](detailed-backlog.md#f01) | A10,A14,A16,R02 |
| Open | [IMP06 — Align development and deployment protection settings](detailed-backlog.md#imp06) | None |
| Open | [IMP07 — Give CLI automation explicit outcomes and exit codes](detailed-backlog.md#imp07) | IMP04 |
| Open | [IMP08 — Make provider usage quota-aware](detailed-backlog.md#imp08) | M03 |
| Open | [IMP09 — Monitor model quality after approval](detailed-backlog.md#imp09) | A06,A16,M03 |
| Open | [IMP10 — Add reproducible experiment comparison and ablation reports](detailed-backlog.md#imp10) | A10,A14,A16 |
| Open | [UX07 — Filters and summary totals behave inconsistently](detailed-backlog.md#ux07) | None |
| Open | [UX08 — Match browsing lacks basic discovery controls](detailed-backlog.md#ux08) | A08 |
| Open | [UX09 — Probability presentation is unbalanced](detailed-backlog.md#ux09) | A05 |
| Open | [UX10 — Diagnostics explanations can reverse interpretation](detailed-backlog.md#ux10) | A18 |
| Open | [UX11 — Navigation and empty states create dead ends](detailed-backlog.md#ux11) | None |
| Open | [UX12 — Terminology and information hierarchy vary across pages](detailed-backlog.md#ux12) | None |
## P3

| Status | Task | Dependencies |
|---|---|---|
| Open | [F02 — Evaluate bounded cross-league parallel execution](detailed-backlog.md#f02) | M03,IMP12 |
| Open | [F03 — Assess Betfair exchange data and liquidity integration](detailed-backlog.md#f03) | A09,M03,IMP08 |
| Open | [F04 — Design opt-in notification delivery](detailed-backlog.md#f04) | A06,M03,IMP09 |
| Open | [IMP11 — Add forecast explanations grounded in actual model output](detailed-backlog.md#imp11) | A14,UX09 |
| Open | [IMP12 — Optimize only against measured workload and quality](detailed-backlog.md#imp12) | A01,A05,A14 |

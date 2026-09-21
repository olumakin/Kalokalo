# Project review and improvement index

All reviews below are dated 2026-09-20. Findings and proposals are documented, **not implemented**. Earlier milestone-completion labels are development history, not evidence of production readiness.

| Document | Coverage | Status |
|---|---|---|
| [Engine/data audit](audit-2026-09-20.md) | A01–A20, prioritized P0–P2 | 20 open findings |
| [UI/UX audit](ui-ux-audit-2026-09-20.md) | UX01–UX12, prioritized P1–P2; accessibility review targets | 12 open findings; rendered verification outstanding |
| [Improvement/enhancement review](improvements-2026-09-20.md) | IMP01–IMP12, prioritized P1–P3 | Six additional defect/risk items and six enhancement proposals; all open |
| [Frontend/backend migration](frontend-backend-migration.md) | API, workers, frontend, data contracts, rollout | Proposed architecture, not implemented |
| [Combined task backlog](../tasks/todo.md) | A, UX, and IMP work references | No audit remediation marked complete |
| [Detailed task specifications](../tasks/detailed-backlog.md) | All 58 audit, migration, verification, empirical-validation and future tasks | Scope, dependencies, acceptance criteria, quality gates and closure evidence; all open |
| [Historical milestones](../tasks/historical-milestones.md) | Previous development completion claims | Preserved history, not current quality-gate acceptance |

The 44 numbered review items are not 44 independent confirmed defects: IMP includes enhancements/refinements, and related topics are cross-referenced. Unverified accessibility/operational checks are not presented as passing or failing runtime tests.

Start with P0 engine/data correctness and the P1 production-integrity work, then establish trustworthy evaluation and storage, then improve the user journey and operations. Add product enhancements only against verified model/data behavior.

Historical documents remain available in [architecture](architecture.md), [invariants](invariants.md), and [gate methodology](gate-evaluation.md). Use their dated audit notices where descriptions conflict with current source findings. For implementation priorities, use the combined backlog and the review documents above.

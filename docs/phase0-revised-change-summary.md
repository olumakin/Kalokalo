# Phase 0 (revised) — Change Summary

Scope reference: "Phase 0 (Revised): Public Read-Only DVPE" plan, approved
before implementation. This document is the discrete change-summary
deliverable requested alongside code, SQL, tests, and the migration
report; see `docs/architecture.md` for the accompanying diagram.

## What shipped, by scope section

**1. CSV upload removed.** The upload branch and its controls are gone
from `app.py`; no test referenced them (grep-verified, see Acceptance
checks below).

**2. Demo data removed from the live app.** `use_demo_history` and
"Generate demo history" are gone from `pages/2_Backtest.py`.
`src/ingestion/demo_data.py` moved to `tests/fixtures/demo_data.py`
(`git mv`) — it is a fixture now, not app code, and no app module imports
it.

**3. Admin authentication.** New `src/webapp/auth.py`:
`is_admin()` (Streamlit OIDC `st.user`, checked against
`st.secrets["admin"]["emails"]`, case-insensitive, degrades to `False` on
any error — including "OIDC not configured yet") and `require_admin()`
(`st.warning("Restricted")` + `st.stop()`). Applied at the top of
`pages/2_Backtest.py` and `pages/3_Ledger.py`; `app.py`'s entire
data-source/run sidebar and the "Run pipeline" action are wrapped in
`if admin:`. `requirements.txt` bumped to `streamlit>=1.42` (the real
OIDC floor).

**4. xG removed from the live path.** `fit_on_xg`, Understat source
selection, and blend-mode controls are gone from `app.py`.
`src/ingestion/understat_xg.py` and `sources.py` are untouched
functionally, each carries a `# WP8: unwired...` docstring note, and their
existing unit tests still pass (12 tests, unaffected — they exercise the
modules directly, not via the UI).

**5. Staking display + gate.** Public cards show fixture, kickoff,
P(home)/P(draw)/P(away), top-2 scorelines (computed at read time from the
stored λ/μ/ρ via `build_score_matrix`/`top_scorelines`, not stored
separately), feed tier, and odds age — no stake, EV, return, or bankroll
figure anywhere in that path. New `gate_status` table
(`league_code, status, run_id, decided_at, approved_by, approved_at`),
seeded to `inconclusive` for all five leagues. Admin view renders a
"Shadow: not validated" expander only where
`status == 'proceed' AND approved_at IS NOT NULL` — true for none of the
seed rows, so it renders nothing today, by design. `stake_shadow` is
computed and written to every prediction row regardless of gate status
(computation/storage is unconditional; only display is gated).

**6. Supabase ledger.** `supabase/schema.sql` rewritten (fresh-install
form) and `supabase/migrations/0002_phase0_revised.sql` added
(idempotent, for an existing live project — `ADD COLUMN IF NOT EXISTS`,
conditional rename of `settlements.odds_close` → `closing_odds_draw`,
`CREATE TABLE IF NOT EXISTS gate_status`, conflict-safe seed insert).
`predictions` gained the full WP3 result schema plus `stake_shadow`,
`bankroll_at_slate`, `run_id`, `git_commit`; unique key on
`(fixture_id, model_version, run_id)`; writes go through
`upsert(..., ignore_duplicates=True)`. `settlements` gained
`home_goals`, `away_goals`, `close_source`, `settled_by`; settlement
writes never touch `predictions`. RLS on both tables: INSERT + SELECT
only, no UPDATE/DELETE policy exists; `gate_status` is SELECT-only for
`anon`. `src/tracking/ledger.py` deleted; `src/pipeline.py`'s CLI path and
the admin web path both write through `src/tracking/supabase_ledger.py`
exclusively. New `is_ledger_online()` check disables "Run pipeline" and
"Record outcome" whenever the Supabase probe fails, with a visible
"Ledger offline" status. One-time migration script:
`scripts/migrate_local_ledger_to_supabase.py` (see Migration report
below).

**7. Public notices.** Session-scoped 18+ confirmation gate blocks all
rendering until accepted; RG notice + "not financial advice" disclaimer
retained from the prior Phase 0 pass; new `pages/4_Terms.py`
(placeholder, headed "DRAFT — pending legal review").

## Acceptance criteria — verification

| # | Criterion | How verified |
|---|---|---|
| 1 | Logged-out visitor can't trigger fit/fetch/backtest/ledger-write/settlement | `tests/test_app_public_access.py` — `AppTest`-driven: no reachable "Run pipeline" button/control for a logged-out session; `load_historical_matches`/`fit_model`/`WalkForwardBacktest.run`/`write_settlement` patched to raise `AssertionError` if invoked, confirmed never called |
| 2 | No code path imports `demo_data` or accepts uploads | `tests/test_public_view_leakage.py::TestNoDemoOrUploadInLiveApp` — source-level string absence checks on `app.py`/`pages/2_Backtest.py`, plus a path-existence check that `demo_data.py` only exists under `tests/fixtures/` |
| 3 | No stake/return/profit figure in the public view | `tests/test_public_view_leakage.py::TestNoStakeFiguresOutsideAdminGate` — AST-based: every `stake_shadow`/`ev_entry` reference in `app.py` must fall inside an `if admin:` (or `if admin and ...:`) block's line range, robust to multiple/nested blocks |
| 4 | Duplicate pipeline run writes no duplicate rows | DB-enforced: unique key `(fixture_id, model_version, run_id)` + `upsert(..., ignore_duplicates=True)`; covered by `tests/test_supabase_ledger.py` |
| 5 | UPDATE/DELETE on both ledger tables rejected by the DB | No UPDATE/DELETE RLS policy defined in `supabase/schema.sql` / the migration — **requires you to confirm in the Supabase SQL editor**, this sandbox cannot reach `supabase.co` (see Manual verification below) |
| 6 | Non-`proceed`/unapproved gate hides shadow stakes in admin view | `app.py`'s admin block checks `status == 'proceed' and approved_at is not None` before rendering the expander; exercised indirectly via `TestFetchGateStatus` in `tests/test_supabase_ledger.py` (no dedicated AppTest for a populated gate row yet — see Known gaps) |
| 7 | Public page renders from Supabase only, no calls to football-data.co.uk / The Odds API | `tests/test_app_public_access.py::test_logged_out_visitor_never_reaches_a_data_fetch_or_model_fit` patches both feed entry points to raise if called from a public session |

Grep-verifiable spot checks (run for real, not just asserted in tests):

```
grep -rn "demo_data\|file_uploader\|use_demo_history\|generate_demo_matches" app.py pages/2_Backtest.py   # → no matches
grep -rin "fit_on_xg\|understat\|blend_mode" app.py                                                        # → no matches
grep -n "stake_pct" app.py                                                                                  # → no matches
```

## Full test suite

`python -m pytest -q` → **228 passed**, 0 failed, 14 warnings (all
`supabase`-client deprecation notices, pre-existing, unrelated to this
change). Includes 8 new `test_auth.py`, 8 new `test_app_public_access.py`,
5 new `test_public_view_leakage.py`, 8 new `test_migrate_local_ledger.py`,
plus extensions to `test_supabase_ledger.py`.

## Migration report

`scripts/migrate_local_ledger_to_supabase.py` is built and unit-tested
(8 passing tests against a mocked Supabase client covering row mapping,
date parsing, CLV-based `closing_odds_draw` reverse-derivation, and the
end-to-end `migrate()` report shape). **It has not been run against your
real `data/ledger.parquet`**: this sandbox has no network egress to
`supabase.co`, so an actual migration run and row count must happen on
your side. Run:

```
python scripts/migrate_local_ledger_to_supabase.py --path data/ledger.parquet
```

It prints a report dict (`rows_read`, `predictions_written`,
`predictions_failed`, `settlements_written`, `settlements_failed`,
`rows_skipped_no_match_id`) — paste that output back if you want it
folded into this document, or file it as a comment on the deployment
ticket.

## Manual steps required outside this session

1. **Register an OIDC provider** (Google Cloud Console is the path of
   least resistance) and populate `.streamlit/secrets.toml`'s `[auth]`
   block plus `[admin] emails = [...]` — documented in full in the
   README's "Public deployment & admin auth" section. This repo cannot
   create that registration itself.
2. **Run `supabase/migrations/0002_phase0_revised.sql`** (existing
   project) or `supabase/schema.sql` (fresh project) in the Supabase SQL
   editor.
3. **Confirm RLS UPDATE/DELETE rejection for real** — from the SQL
   editor or a REST client using the `anon` key, attempt an UPDATE and a
   DELETE against `predictions` and `settlements`; both should fail. This
   sandbox's network policy blocks `supabase.co`, so it could not be
   exercised live here.
4. **Run the migration script** against the real local ledger file, per
   above, and confirm the printed row counts look right.

## Known gaps / deviations, stated explicitly

- No AppTest exercises criterion 6 (gate-hides-stakes) with a populated,
  `proceed`-status `gate_status` row — the check was verified by code
  inspection and by `TestFetchGateStatus`'s coverage of the read path,
  not by an end-to-end admin-view render with mocked "approved" gate
  data. Worth adding if this becomes a frequently-touched code path.
- The RLS no-UPDATE/no-DELETE guarantee and the OIDC provider
  registration cannot be verified from inside this sandbox (no network
  egress to `supabase.co`) — flagged as manual steps above rather than
  claimed as done.
- `bankroll_at_slate` is a placeholder notional unit
  (`config/settings.yaml`'s `edge.shadow_bankroll_units`, default `1.0`),
  not a real bankroll-tracking figure — there is no bankroll-tracking
  system in this app today.
- `pages/1_Model_Diagnostics.py` was left untouched (not in the task's
  admin-only page list); it already degrades gracefully for a public
  visitor because `st.session_state["model"]` is only ever populated by
  an admin's own pipeline run.

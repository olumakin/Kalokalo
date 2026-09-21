# DVPE Architecture — Phase 0 (revised)

## Actors and trust boundary

```
                         ┌───────────────────────────┐
                         │   Public visitor (anon)   │
                         └─────────────┬─────────────┘
                                        │ HTTPS
                                        ▼
                         ┌───────────────────────────┐
                         │  Streamlit app (app.py +   │
                         │  pages/*.py)                │
                         │                             │
                         │  age gate → is_admin() ─────┼──▶ False for
                         │       │                      │   every anon
                         │       ▼                      │   visitor
                         │  PUBLIC VIEW:                │
                         │   fetch_latest_predictions() │
                         │   (Supabase SELECT only)     │
                         └─────────────┬───────────────┘
                                        │ anon key, RLS: SELECT-only
                                        ▼
                         ┌───────────────────────────┐
                         │        Supabase            │
                         │  predictions (RLS: I+S)    │
                         │  settlements (RLS: I+S)     │
                         │  gate_status (RLS: S only) │
                         │  no UPDATE/DELETE policy    │
                         │  on any table                │
                         └───────────────────────────┘
                                        ▲
                                        │ anon key, RLS: INSERT+SELECT
                         ┌─────────────┴───────────────┐
                         │  ADMIN VIEW (is_admin()==True)│
                         │   - st.login() / st.user       │
                         │     (Streamlit OIDC)           │
                         │   - email checked against      │
                         │     st.secrets["admin"]["emails"]│
                         │   - Run pipeline:               │
                         │       load_historical_matches   │
                         │       → fit_model                │
                         │       → get_upcoming_fixtures    │
                         │       → build_predictions        │
                         │       → record_predictions_      │
                         │         to_supabase (INSERT,     │
                         │         conflict-ignore)         │
                         │   - Shadow stakes shown only      │
                         │     where gate_status.status ==   │
                         │     'proceed' AND approved_at set │
                         │   - Backtest page (2_Backtest.py) │
                         │   - Ledger/settlement page        │
                         │     (3_Ledger.py) → write_        │
                         │     settlement (INSERT into       │
                         │     settlements, never updates    │
                         │     predictions)                  │
                         └───────────────┬────────────────┘
                                         │
                    ┌────────────────────┼─────────────────────┐
                    ▼                    ▼                     ▼
          football-data.co.uk     The Odds API          src/models
          (historical results,    (live odds, admin       Dixon-Coles fit,
           admin-triggered only)   run only)               10x10 simulator
```

## Key properties enforced by this structure

- **No unauthenticated write or compute path.** Every branch that fits a
  model, calls an external data feed, or writes to Supabase is inside
  `if admin:` (i.e. gated by `is_admin()`), verified by
  `tests/test_app_public_access.py` via `AppTest` (button/control
  absence) and by patching `load_historical_matches`/`fit_model` to raise
  if ever called from a logged-out session.
- **Public page never fits a model or calls a data feed.** Its only data
  path is `fetch_latest_predictions()`, a Supabase `SELECT` — confirmed by
  the same test file; no `requests.get` to football-data.co.uk or The Odds
  API exists outside the admin branch.
- **Supabase is the only ledger.** `src/tracking/ledger.py` (local
  Parquet/CSV file) is deleted; `src/pipeline.py`'s CLI path and the admin
  web path both write through `src/tracking/supabase_ledger.py`.
- **RLS is the actual enforcement point for "no update/delete."** The app
  never issues an UPDATE or DELETE against `predictions` or `settlements`
  in code, and the database additionally has no policy permitting either —
  a defense-in-depth pair, not just an app-level convention. `gate_status`
  is SELECT-only for the `anon` role; nothing in the app writes it (manual
  DB action by design).
- **Duplicate-run safety is a DB constraint, not app logic.**
  `predictions` has a unique key on `(fixture_id, model_version, run_id)`;
  writes use `upsert(..., ignore_duplicates=True)`.
- **Staking is gated per league, default "not eligible."** `gate_status`
  seeds all five leagues to `inconclusive`; a shadow stake is computed and
  stored on every admin run (so the pipeline itself never blocks), but is
  only *rendered* in the admin UI when `status == 'proceed' AND
  approved_at IS NOT NULL` — currently true for none of them.
- **xG/Understat is present in the repo but not reachable from any live
  code path** (`src/ingestion/understat_xg.py`, `sources.py`) — deferred
  to WP8, verified by grep-based tests
  (`tests/test_public_view_leakage.py`).

## What changed vs. the pre-Phase-0-revised diagram

| Old | New |
|---|---|
| Single ungated `app.py` script — anyone could trigger fit/fetch/write | Split into public view (Supabase read-only) and admin view (`is_admin()`-gated) |
| Local `src/tracking/ledger.py` (Parquet/CSV) + Supabase, written in parallel | Local ledger deleted; Supabase is the sole ledger for both the CLI and the web app |
| CSV upload / demo data selectable by any visitor | Removed entirely from the live app; demo data survives only as a test fixture (`tests/fixtures/demo_data.py`) |
| Understat xG / multi-source blend selectable in the sidebar | Unwired from `app.py`; modules retained, untested-by-UI, WP8-flagged |
| No staking gate — EV/stake shown to all visitors | `gate_status` table + admin-only "Shadow: not validated" display, default not eligible |
| No admin authentication | Streamlit OIDC (`st.login`/`st.user`) + email allow-list in `st.secrets["admin"]["emails"]` |
| No age gate / terms page | Age confirmation gate (session-scoped) + `pages/4_Terms.py` (draft) |

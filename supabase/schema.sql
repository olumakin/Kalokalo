-- DVPE Phase 0 (revised): durable remote prediction ledger + gate status.
--
-- Canonical full schema for a FRESH Supabase project (a new deployment
-- with no tables yet). If your project already ran the original Phase 0
-- schema.sql, DO NOT re-run this file — it will fail with "relation
-- already exists" the same way a partial re-run always does. Instead run
-- supabase/migrations/0002_phase0_revised.sql, which ALTERs your existing
-- tables in place and only adds what's new.
--
-- Run this once in the Supabase SQL Editor (Project -> SQL Editor -> New
-- query). The app connects with the anon/publishable key only, which is
-- why RLS grants exactly INSERT + SELECT on predictions/settlements and
-- SELECT ONLY on gate_status — predictions and settlements are append-only
-- from the app's point of view (no UPDATE or DELETE policy on either);
-- gate_status is read-only from the app's point of view (no INSERT policy
-- either) — approving a league is a manual operation done by the table
-- owner (Supabase dashboard / service-role key), not an app feature.

-- Main predictions table. actual_home_goals/actual_away_goals/
-- actual_outcome_idx exist for parity with the WP3 evaluation harness
-- (src/validation/gate_harness.py), which fills them at write time from
-- historical hindsight; the *live* app writes a prediction before the
-- match is played, so those three columns are always NULL for live rows
-- and stay that way forever (no UPDATE policy) -- the real outcome lives
-- in `settlements`, joined by fixture_id, never backfilled here.
CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    model_version TEXT NOT NULL,
    git_commit TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    fixture_id TEXT NOT NULL,
    match_date TIMESTAMPTZ,
    league TEXT,
    season TEXT,
    matchweek TEXT,
    home_id TEXT,
    away_id TEXT,
    p_home NUMERIC,
    p_draw NUMERIC,
    p_away NUMERIC,
    lambda_val NUMERIC,
    mu_val NUMERIC,
    rho_val NUMERIC,
    xi_val NUMERIC,
    converged BOOLEAN,
    fallback_used BOOLEAN,
    skip_reason TEXT,
    -- Legacy single-price fields from the original Phase 0 schema (draw
    -- price only) -- kept and still populated for backward compatibility.
    odds_entry NUMERIC,
    price_source TEXT,
    ev_entry NUMERIC,
    -- Full three-way entry price (WP3 schema).
    entry_home NUMERIC,
    entry_draw NUMERIC,
    entry_away NUMERIC,
    entry_source TEXT,
    -- Closing price: NULL for every live-app row today -- nothing in the
    -- live path re-prices a fixture near kickoff yet (see WP3 gate_harness
    -- for the only code that populates this, on historical data).
    close_home NUMERIC,
    close_draw NUMERIC,
    close_away NUMERIC,
    close_source TEXT,
    retail_draw NUMERIC,
    market_p_home NUMERIC,
    market_p_draw NUMERIC,
    market_p_away NUMERIC,
    actual_home_goals INTEGER,
    actual_away_goals INTEGER,
    actual_outcome_idx SMALLINT,
    -- Shadow staking (Phase 0 revised): computed and stored on every run
    -- regardless of gate_status; the admin UI only *displays* it when a
    -- league's gate_status is 'proceed' with approved_at set. Public
    -- visitors never see either column.
    stake_shadow NUMERIC,
    bankroll_at_slate NUMERIC,
    UNIQUE (fixture_id, model_version, run_id)
);

-- Per-league staking eligibility gate. Read-only from the app; approving
-- a league (setting status/approved_by/approved_at) is done directly in
-- Supabase, not through any UI in this app.
CREATE TABLE gate_status (
    league_code TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'inconclusive',
    run_id UUID,
    decided_at TIMESTAMPTZ,
    approved_by TEXT,
    approved_at TIMESTAMPTZ
);

-- Separate settlements table (append-only; never mutates `predictions`).
CREATE TABLE settlements (
    id SERIAL PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    home_goals INTEGER,
    away_goals INTEGER,
    closing_odds_draw NUMERIC,
    close_source TEXT,
    actual_result TEXT,
    settled_by TEXT,
    settled_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enforce append-only access for the anon (publishable) key.
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon insert" ON predictions FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "Allow anon select" ON predictions FOR SELECT TO anon USING (true);

ALTER TABLE settlements ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon insert" ON settlements FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "Allow anon select" ON settlements FOR SELECT TO anon USING (true);

ALTER TABLE gate_status ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon select" ON gate_status FOR SELECT TO anon USING (true);

INSERT INTO gate_status (league_code, status) VALUES
    ('E0', 'inconclusive'),
    ('SP1', 'inconclusive'),
    ('I1', 'inconclusive'),
    ('D1', 'inconclusive'),
    ('F1', 'inconclusive');

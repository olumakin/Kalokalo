-- DVPE Phase 0: durable remote prediction ledger.
--
-- Run this once in the Supabase SQL Editor (Project -> SQL Editor -> New
-- query). The app connects with the anon/publishable key only, which is
-- why RLS grants exactly INSERT + SELECT below and nothing else —
-- predictions and settlements are both append-only from the app's point
-- of view; there is no UPDATE or DELETE policy on either table.

-- Main predictions table.
CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    model_version TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    fixture_id TEXT NOT NULL,
    match_date TIMESTAMPTZ,
    league TEXT,
    season TEXT,
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
    skip_reason TEXT,
    odds_entry NUMERIC,
    price_source TEXT,
    ev_entry NUMERIC,
    UNIQUE (fixture_id, model_version, run_id)
);

-- Separate settlements table (append-only; never mutates `predictions`).
CREATE TABLE settlements (
    id SERIAL PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    odds_close NUMERIC,
    actual_result TEXT,
    settled_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enforce append-only access for the anon (publishable) key.
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon insert" ON predictions FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "Allow anon select" ON predictions FOR SELECT TO anon USING (true);

ALTER TABLE settlements ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon insert" ON settlements FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "Allow anon select" ON settlements FOR SELECT TO anon USING (true);

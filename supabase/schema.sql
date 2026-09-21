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
    UNIQUE (fixture_id, model_version, run_id),
    CONSTRAINT chk_probs CHECK (
        (p_home IS NULL OR (p_home >= 0 AND p_home <= 1)) AND
        (p_draw IS NULL OR (p_draw >= 0 AND p_draw <= 1)) AND
        (p_away IS NULL OR (p_away >= 0 AND p_away <= 1))
    ),
    CONSTRAINT chk_odds CHECK (odds_entry IS NULL OR odds_entry > 1.0)
);

-- Separate settlements table (append-only; never mutates `predictions`).
CREATE TABLE settlements (
    id SERIAL PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    odds_close NUMERIC,
    actual_result TEXT,
    settled_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_settle_odds CHECK (odds_close IS NULL OR odds_close > 1.0)
);

-- Enforce security and least privilege (A07):
-- Reads are allowed for authenticated and anon users.
-- Writes require authenticated sessions or backend service_role (preventing anonymous record forging).
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow read predictions" ON predictions FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Allow write predictions" ON predictions FOR INSERT TO authenticated, service_role WITH CHECK (true);

ALTER TABLE settlements ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow read settlements" ON settlements FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Allow write settlements" ON settlements FOR INSERT TO authenticated, service_role WITH CHECK (true);

-- Stage 1: Immutable Prospective Validation Event Store (Append-Only)
CREATE TABLE prospective_events (
    id SERIAL PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    epoch_id TEXT,
    idempotency_key TEXT UNIQUE NOT NULL,
    created_at_utc TIMESTAMPTZ DEFAULT NOW(),
    test_flag TEXT DEFAULT 'PROSPECTIVE',
    target_event_id TEXT,
    code_identity TEXT,
    semantic_config_identity TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_test_flag CHECK (test_flag IN ('TEST_ONLY_NON_PROSPECTIVE', 'PROSPECTIVE')),
    CONSTRAINT chk_event_epoch CHECK (
        test_flag = 'TEST_ONLY_NON_PROSPECTIVE' OR (epoch_id IS NOT NULL AND LENGTH(epoch_id) > 0)
    )
);

CREATE INDEX idx_events_epoch ON prospective_events (epoch_id);
CREATE INDEX idx_events_idempotency ON prospective_events (idempotency_key);
CREATE INDEX idx_events_type ON prospective_events (event_type);

ALTER TABLE prospective_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow read prospective_events" ON prospective_events FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Allow write prospective_events" ON prospective_events FOR INSERT TO authenticated, service_role WITH CHECK (true);
-- Append-only enforcement: No UPDATE or DELETE policy granted on prospective_events.

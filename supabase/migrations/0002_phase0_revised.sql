-- DVPE Phase 0 (revised): incremental migration for a project that
-- already ran the original supabase/schema.sql (the Phase 0 predictions/
-- settlements tables exist). Run this once in the Supabase SQL Editor.
-- Every ADD COLUMN uses IF NOT EXISTS, so it's safe to re-run.

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS git_commit TEXT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS matchweek TEXT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS entry_home NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS entry_draw NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS entry_away NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS entry_source TEXT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS close_home NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS close_draw NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS close_away NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS close_source TEXT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS retail_draw NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS market_p_home NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS market_p_draw NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS market_p_away NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS actual_home_goals INTEGER;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS actual_away_goals INTEGER;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS actual_outcome_idx SMALLINT;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS stake_shadow NUMERIC;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS bankroll_at_slate NUMERIC;

-- settlements: add the new columns, then rename odds_close now that
-- 3_Ledger.py is being rewired to write this table for the first time
-- (nothing live has read/written `odds_close` yet, so this is a rename,
-- not a breaking change to any working path).
ALTER TABLE settlements ADD COLUMN IF NOT EXISTS home_goals INTEGER;
ALTER TABLE settlements ADD COLUMN IF NOT EXISTS away_goals INTEGER;
ALTER TABLE settlements ADD COLUMN IF NOT EXISTS close_source TEXT;
ALTER TABLE settlements ADD COLUMN IF NOT EXISTS settled_by TEXT;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'settlements' AND column_name = 'odds_close'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'settlements' AND column_name = 'closing_odds_draw'
    ) THEN
        ALTER TABLE settlements RENAME COLUMN odds_close TO closing_odds_draw;
    END IF;
END $$;

-- New gate_status table (see supabase/schema.sql for full column/RLS
-- rationale). Skipped if it already exists, so this migration is
-- idempotent as a whole.
CREATE TABLE IF NOT EXISTS gate_status (
    league_code TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'inconclusive',
    run_id UUID,
    decided_at TIMESTAMPTZ,
    approved_by TEXT,
    approved_at TIMESTAMPTZ
);

ALTER TABLE gate_status ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon select" ON gate_status;
CREATE POLICY "Allow anon select" ON gate_status FOR SELECT TO anon USING (true);

INSERT INTO gate_status (league_code, status) VALUES
    ('E0', 'inconclusive'),
    ('SP1', 'inconclusive'),
    ('I1', 'inconclusive'),
    ('D1', 'inconclusive'),
    ('F1', 'inconclusive')
ON CONFLICT (league_code) DO NOTHING;

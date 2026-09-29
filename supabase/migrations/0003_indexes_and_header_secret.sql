-- DVPE Phase 2 hardening: indexes for fetch_latest_predictions'/
-- fetch_settlements' query patterns, and a shared-secret header check on
-- the anon INSERT policies (closing the "anyone with the public anon
-- key can spam junk rows" gap) without ever using the service-role key.
--
-- IMPORTANT — run this only after the corresponding app code (the one
-- that sends the x-app-secret header on every Supabase request) is
-- already deployed and confirmed working. Running this first, before
-- the app sends the header, will make every admin write fail (safely
-- logged, not a crash, but functionally broken) until the app catches
-- up. Idempotent — safe to re-run.

CREATE INDEX IF NOT EXISTS idx_predictions_created_at_desc ON predictions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_run_id ON predictions (run_id);
CREATE INDEX IF NOT EXISTS idx_settlements_fixture_id ON settlements (fixture_id);

-- Replace the wide-open "Allow anon insert" policies with ones that
-- also require a shared secret in a custom request header. The anon
-- (publishable) key alone is no longer sufficient to write a row;
-- someone would need both the key AND this secret. Still no
-- service-role key anywhere, and RLS is still insert+select only —
-- this tightens WITH CHECK, it doesn't touch UPDATE/DELETE (still no
-- policy grants either, on either table).
--
-- Replace 'REPLACE_WITH_YOUR_APP_SECRET' below with the exact value
-- from SUPABASE_APP_SECRET in your Streamlit secrets before running.

DROP POLICY IF EXISTS "Allow anon insert" ON predictions;
DROP POLICY IF EXISTS "Allow anon insert with secret" ON predictions;
CREATE POLICY "Allow anon insert with secret" ON predictions FOR INSERT TO anon
WITH CHECK (
    current_setting('request.headers', true)::json ->> 'x-app-secret' = 'REPLACE_WITH_YOUR_APP_SECRET'
);

DROP POLICY IF EXISTS "Allow anon insert" ON settlements;
DROP POLICY IF EXISTS "Allow anon insert with secret" ON settlements;
CREATE POLICY "Allow anon insert with secret" ON settlements FOR INSERT TO anon
WITH CHECK (
    current_setting('request.headers', true)::json ->> 'x-app-secret' = 'REPLACE_WITH_YOUR_APP_SECRET'
);

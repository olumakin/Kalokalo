-- Reverts 0003/0004's header-secret INSERT requirement. In production
-- it rejected every single write with a generic 42501 ("new row
-- violates row-level security policy"), and after verifying both ends
-- of the chain -- the Python client sends the header correctly
-- (confirmed: httpx preserves the exact lowercase casing given), and
-- PostgREST receives and exposes it correctly (confirmed via a
-- temporary debug_headers() RPC + curl) -- the exact mismatch inside
-- Streamlit Cloud's actual runtime could not be pinned down further
-- without a debug deploy cycle this session couldn't reliably drive.
-- Reverting to the simple, previously-working anon INSERT policy
-- rather than leave live writes broken while chasing it blind.
--
-- Indexes and the narrowed fetch_latest_predictions query from the
-- same rollout are unaffected and stay in place -- this only touches
-- the two INSERT policies, plus removing the temporary debug function.

DROP POLICY IF EXISTS "Allow anon insert with secret" ON predictions;
CREATE POLICY "Allow anon insert" ON predictions FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon insert with secret" ON settlements;
CREATE POLICY "Allow anon insert" ON settlements FOR INSERT TO anon WITH CHECK (true);

DROP FUNCTION IF EXISTS debug_headers();

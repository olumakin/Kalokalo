-- Fixes 0003_indexes_and_header_secret.sql's INSERT policies: every
-- admin pipeline write has been rejected with 42501 ("new row violates
-- row-level security policy") since that migration ran. Root cause:
-- `current_setting('request.headers', true)::json ->> 'x-app-secret'`
-- does an exact-case JSON key lookup, but HTTP header names are
-- case-insensitive by spec and commonly get normalized to title-case
-- (e.g. "X-App-Secret") somewhere in the request path -- PostgREST
-- stores request.headers using whatever casing it actually received,
-- so the lowercase key lookup silently finds nothing and the policy
-- always evaluates false.
--
-- Fix: find the header by a case-insensitive key match instead of an
-- exact-case lookup, so this works regardless of how the header got
-- normalized in transit.
--
-- Replace 'REPLACE_WITH_YOUR_APP_SECRET' with the exact value from
-- SUPABASE_APP_SECRET in your Streamlit secrets before running --
-- re-paste it fresh rather than trusting memory, since a stray
-- whitespace/character mismatch here produces this exact same 42501
-- error with no more detail than before.

DROP POLICY IF EXISTS "Allow anon insert with secret" ON predictions;
CREATE POLICY "Allow anon insert with secret" ON predictions FOR INSERT TO anon
WITH CHECK (
    EXISTS (
        SELECT 1
        FROM jsonb_each_text(current_setting('request.headers', true)::jsonb) AS h(key, value)
        WHERE lower(h.key) = 'x-app-secret' AND h.value = 'REPLACE_WITH_YOUR_APP_SECRET'
    )
);

DROP POLICY IF EXISTS "Allow anon insert with secret" ON settlements;
CREATE POLICY "Allow anon insert with secret" ON settlements FOR INSERT TO anon
WITH CHECK (
    EXISTS (
        SELECT 1
        FROM jsonb_each_text(current_setting('request.headers', true)::jsonb) AS h(key, value)
        WHERE lower(h.key) = 'x-app-secret' AND h.value = 'REPLACE_WITH_YOUR_APP_SECRET'
    )
);

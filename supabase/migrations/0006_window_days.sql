-- Logs the rolling-window length (days) each prediction's historical
-- data was trimmed to (src.pipeline.trim_to_rolling_window), so a
-- viewer can tell after the fact whether a prediction came from the
-- WP3-validated 730-day window or an admin experiment at a different
-- one. Run this once in the Supabase SQL Editor; safe to re-run.

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS window_days INTEGER;

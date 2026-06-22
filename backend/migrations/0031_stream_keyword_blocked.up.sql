-- Add is_keyword_blocked to stream table (mirrors media.is_keyword_blocked).
ALTER TABLE stream ADD COLUMN IF NOT EXISTS is_keyword_blocked BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_stream_keyword_blocked ON stream (id) WHERE is_keyword_blocked = true;

-- Trigger: recompute on stream INSERT/UPDATE using stream-scoped keywords (scope 'all' or 'stream').
CREATE OR REPLACE FUNCTION check_stream_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    lname text := LOWER(NEW.name);
BEGIN
    NEW.is_keyword_blocked := (
        EXISTS (
            SELECT 1 FROM keyword_filters kf
            WHERE kf.is_active = true
              AND kf.scope IN ('all', 'stream')
              AND position(LOWER(kf.keyword) IN lname) > 0
        )
        AND NOT EXISTS (
            SELECT 1 FROM keyword_whitelist kw
            WHERE position(LOWER(kw.phrase) IN lname) > 0
        )
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_stream_keyword_blocked
    BEFORE INSERT OR UPDATE OF name ON stream
    FOR EACH ROW EXECUTE FUNCTION check_stream_keyword_blocked();

-- Force batch recompute on next startup.
DELETE FROM keyword_sync_state WHERE id = 'stream-keyword-blocked-recompute';

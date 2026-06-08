-- Add precomputed is_keyword_blocked boolean to avoid O(N_rows × N_keywords) per-query cost.
-- A partial index makes COUNT queries on blocked media nearly free.

ALTER TABLE media ADD COLUMN IF NOT EXISTS is_keyword_blocked boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_media_keyword_blocked
    ON media (is_keyword_blocked) WHERE is_keyword_blocked = true;

-- Bulk-recompute is_keyword_blocked for every media row from the live keyword tables.
-- Called from Rust after every keyword list change (admin API or file sync).
CREATE OR REPLACE FUNCTION recompute_all_keyword_blocked()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE media
    SET is_keyword_blocked = (
        EXISTS (
            SELECT 1 FROM keyword_filters kf
            WHERE kf.is_active = true
              AND position(LOWER(kf.keyword) IN LOWER(media.title)) > 0
        )
        AND NOT EXISTS (
            SELECT 1 FROM keyword_whitelist kw
            WHERE position(LOWER(kw.phrase) IN LOWER(media.title)) > 0
        )
    );
END;
$$;

-- Per-row trigger: keep is_keyword_blocked in sync when a title is inserted/updated.
CREATE OR REPLACE FUNCTION check_media_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.is_keyword_blocked := (
        EXISTS (
            SELECT 1 FROM keyword_filters kf
            WHERE kf.is_active = true
              AND position(LOWER(kf.keyword) IN LOWER(NEW.title)) > 0
        )
        AND NOT EXISTS (
            SELECT 1 FROM keyword_whitelist kw
            WHERE position(LOWER(kw.phrase) IN LOWER(NEW.title)) > 0
        )
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_media_title_keyword_check ON media;
CREATE TRIGGER trg_media_title_keyword_check
    BEFORE INSERT OR UPDATE OF title ON media
    FOR EACH ROW EXECUTE FUNCTION check_media_keyword_blocked();

-- Initial backfill
SELECT recompute_all_keyword_blocked();

-- Extend is_keyword_blocked to cover description keywords and TMDB adult flag.
--
-- Before: only title was checked for keyword matches.
-- After:  a media row is blocked when:
--   • adult = true (as reported by TMDB), OR
--   • title OR description contains an active keyword  (AND not whitelisted)
--
-- The trigger is extended to fire on description and adult column changes too.
-- The stored recompute version is deleted so Rust re-runs the batch UPDATE on
-- the next startup and back-fills all existing rows with the new logic.

-- Update per-row trigger function
CREATE OR REPLACE FUNCTION check_media_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.is_keyword_blocked := (
        NEW.adult = true
        OR (
            EXISTS (
                SELECT 1 FROM keyword_filters kf
                WHERE kf.is_active = true
                  AND (
                      position(LOWER(kf.keyword) IN LOWER(NEW.title)) > 0
                      OR (NEW.description IS NOT NULL
                          AND position(LOWER(kf.keyword) IN LOWER(NEW.description)) > 0)
                  )
            )
            AND NOT EXISTS (
                SELECT 1 FROM keyword_whitelist kw
                WHERE position(LOWER(kw.phrase) IN LOWER(NEW.title)) > 0
                   OR (NEW.description IS NOT NULL
                       AND position(LOWER(kw.phrase) IN LOWER(NEW.description)) > 0)
            )
        )
    );
    RETURN NEW;
END;
$$;

-- Re-create trigger to also fire when description or adult changes
DROP TRIGGER IF EXISTS trg_media_title_keyword_check ON media;
CREATE TRIGGER trg_media_title_keyword_check
    BEFORE INSERT OR UPDATE OF title, description, adult ON media
    FOR EACH ROW EXECUTE FUNCTION check_media_keyword_blocked();

-- Force Rust to re-run the batch recompute on next startup so existing rows
-- are back-filled with the new logic (adult flag + description keyword check).
DELETE FROM keyword_sync_state WHERE id = 'keyword-blocked-recompute';

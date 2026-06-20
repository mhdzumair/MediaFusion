-- Revert is_keyword_blocked to title-only matching, no adult flag.

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

DELETE FROM keyword_sync_state WHERE id = 'keyword-blocked-recompute';

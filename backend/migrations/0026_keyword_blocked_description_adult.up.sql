-- Extend is_keyword_blocked to cover description keywords and TMDB adult flag.
--
-- Before: only title was checked for keyword matches.
-- After:  a media row is blocked when:
--   • adult = true (as reported by TMDB), OR
--   • title OR description matches a keyword pattern  (AND not whitelisted)
--
-- The trigger is extended to fire on description and adult column changes too.
-- The stored recompute version is deleted so Rust re-runs the batch UPDATE on
-- the next startup and back-fills all existing rows with the new logic.
--
-- Perf: trigger pre-computes LOWER(title/description) into locals (avoids
-- recomputing per keyword). recompute_all_keyword_blocked() compiles all
-- keywords into a single POSIX regex alternation (kw1|kw2|...) so PostgreSQL
-- runs one NFA pass per row instead of N_keywords position() scans.

-- Update per-row trigger function.
-- Trigger fires on single rows so a correlated scan of keyword_filters is fine;
-- the main optimisation here is computing LOWER() once via locals.
CREATE OR REPLACE FUNCTION check_media_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ltitle text := LOWER(NEW.title);
    ldesc  text := LOWER(COALESCE(NEW.description, ''));
BEGIN
    NEW.is_keyword_blocked := (
        NEW.adult = true
        OR (
            EXISTS (
                SELECT 1 FROM keyword_filters kf
                WHERE kf.is_active = true
                  AND (
                      position(LOWER(kf.keyword) IN ltitle) > 0
                      OR position(LOWER(kf.keyword) IN ldesc) > 0
                  )
            )
            AND NOT EXISTS (
                SELECT 1 FROM keyword_whitelist kw
                WHERE position(LOWER(kw.phrase) IN ltitle) > 0
                   OR position(LOWER(kw.phrase) IN ldesc) > 0
            )
        )
    );
    RETURN NEW;
END;
$$;

-- Re-create trigger to also fire when description or adult changes.
DROP TRIGGER IF EXISTS trg_media_title_keyword_check ON media;
CREATE TRIGGER trg_media_title_keyword_check
    BEFORE INSERT OR UPDATE OF title, description, adult ON media
    FOR EACH ROW EXECUTE FUNCTION check_media_keyword_blocked();

-- Bulk-recompute function: compiles keywords into a single regex alternation
-- so PostgreSQL runs one NFA evaluation per row instead of N_keywords scans.
-- regexp_replace escapes POSIX metacharacters in each keyword before joining.
CREATE OR REPLACE FUNCTION recompute_all_keyword_blocked()
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    kw_pattern text;
    wl_pattern text;
BEGIN
    SELECT '(' || string_agg(
               regexp_replace(LOWER(keyword), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY keyword
           ) || ')'
    INTO kw_pattern
    FROM keyword_filters WHERE is_active = true;

    SELECT '(' || string_agg(
               regexp_replace(LOWER(phrase), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY phrase
           ) || ')'
    INTO wl_pattern
    FROM keyword_whitelist;

    WITH computed AS (
        SELECT id,
               adult = true
               OR (
                   kw_pattern IS NOT NULL
                   AND (
                       LOWER(title) ~ kw_pattern
                       OR LOWER(COALESCE(description, '')) ~ kw_pattern
                   )
                   AND (
                       wl_pattern IS NULL
                       OR NOT (
                           LOWER(title) ~ wl_pattern
                           OR LOWER(COALESCE(description, '')) ~ wl_pattern
                       )
                   )
               ) AS new_blocked
        FROM media
    )
    UPDATE media m
    SET is_keyword_blocked = c.new_blocked
    FROM computed c
    WHERE m.id = c.id
      AND m.is_keyword_blocked IS DISTINCT FROM c.new_blocked;
END;
$$;

-- Force Rust to re-run the batch recompute on next startup so existing rows
-- are back-filled with the new logic (adult flag + description keyword check).
DELETE FROM keyword_sync_state WHERE id = 'keyword-blocked-recompute';

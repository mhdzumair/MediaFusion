-- Block all media that carry the "Adult" genre.
--
-- The existing check_media_keyword_blocked trigger fires on UPDATE OF adult,
-- so setting adult = true here automatically sets is_keyword_blocked = true too.
--
-- Two-part fix:
--   1. Backfill existing media that already have the Adult genre linked.
--   2. Add a trigger on media_genre_link so future Adult genre links are caught immediately.

-- ── 1. Backfill ───────────────────────────────────────────────────────────────
UPDATE media
SET adult = true
WHERE adult = false
  AND EXISTS (
    SELECT 1
    FROM media_genre_link mgl
    JOIN genre g ON g.id = mgl.genre_id
    WHERE mgl.media_id = media.id
      AND LOWER(g.name) = 'adult'
  );

-- ── 2. Forward trigger ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION propagate_adult_genre()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Only act when the linked genre is "Adult" and the media isn't already flagged.
    IF EXISTS (
        SELECT 1 FROM genre WHERE id = NEW.genre_id AND LOWER(name) = 'adult'
    ) THEN
        UPDATE media SET adult = true
        WHERE id = NEW.media_id AND adult = false;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_adult_genre_link ON media_genre_link;
CREATE TRIGGER trg_adult_genre_link
    AFTER INSERT ON media_genre_link
    FOR EACH ROW EXECUTE FUNCTION propagate_adult_genre();

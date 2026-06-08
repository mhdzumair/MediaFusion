DROP TRIGGER IF EXISTS trg_media_title_keyword_check ON media;
DROP FUNCTION IF EXISTS check_media_keyword_blocked();
DROP FUNCTION IF EXISTS recompute_all_keyword_blocked();
DROP INDEX IF EXISTS idx_media_keyword_blocked;
ALTER TABLE media DROP COLUMN IF EXISTS is_keyword_blocked;

DROP TRIGGER IF EXISTS trg_stream_keyword_blocked ON stream;
DROP FUNCTION IF EXISTS check_stream_keyword_blocked();
DROP INDEX IF EXISTS idx_stream_keyword_blocked;
ALTER TABLE stream DROP COLUMN IF EXISTS is_keyword_blocked;
DELETE FROM keyword_sync_state WHERE id = 'stream-keyword-blocked-recompute';

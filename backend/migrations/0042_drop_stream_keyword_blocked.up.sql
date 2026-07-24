-- Stream keyword blocking is runtime-only (in-memory cache at serve/scrape time).
-- Drop the precomputed column, per-row trigger, and file-sourced stream keyword rows.

DROP TRIGGER IF EXISTS trg_stream_keyword_blocked ON stream;
DROP FUNCTION IF EXISTS check_stream_keyword_blocked();
DROP INDEX IF EXISTS idx_stream_keyword_blocked;
ALTER TABLE stream DROP COLUMN IF EXISTS is_keyword_blocked;

DELETE FROM keyword_sync_state WHERE id IN (
    'stream-keywords',
    'stream-keyword-blocked-recompute',
    'stream-keyword-blocked-recompute-lease',
    'stream-keyword-blocked-recompute-request'
);

-- Force media file re-import on next startup (fixes legacy scope overlap with stream file).
DELETE FROM keyword_sync_state WHERE id = 'media-keywords';

DELETE FROM keyword_filters WHERE source = 'file' AND scope = 'stream';

-- no-transaction
-- HOT-update and missing-index fixes.
--
-- 1. Drop btree(updated_at) on stream and media. updated_at is never a query
--    predicate; these indexes block Heap-Only-Tuple updates on every ingest write.
-- 2. Add functional lower(title) index so LOWER(title) = LOWER($1) lookups can
--    use an index instead of seq-scanning media.
-- 3. Add partial FK-support indexes for media.*_by_user_id columns that were
--    missing, causing a seq-scan of media per missing column on DELETE FROM users.
-- 4. Lower fillfactor on stream and media to reserve per-page headroom for HOT.
--    Takes effect for future page allocations; no table rewrite is required.

DROP INDEX CONCURRENTLY IF EXISTS ix_stream_updated_at;
DROP INDEX CONCURRENTLY IF EXISTS ix_media_updated_at;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_lower_title
    ON public.media (lower(title), type);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_blocked_by_user
    ON public.media (blocked_by_user_id)
    WHERE blocked_by_user_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_last_refreshed_by_user
    ON public.media (last_refreshed_by_user_id)
    WHERE last_refreshed_by_user_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_last_scraped_by_user
    ON public.media (last_scraped_by_user_id)
    WHERE last_scraped_by_user_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_migrated_by_user
    ON public.media (migrated_by_user_id)
    WHERE migrated_by_user_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_episode_created_by_user
    ON public.episode (created_by_user_id)
    WHERE created_by_user_id IS NOT NULL;

ALTER TABLE public.stream SET (fillfactor = 85);
ALTER TABLE public.media  SET (fillfactor = 85);

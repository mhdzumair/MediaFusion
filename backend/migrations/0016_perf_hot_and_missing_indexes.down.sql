ALTER TABLE public.media  RESET (fillfactor);
ALTER TABLE public.stream RESET (fillfactor);

DROP INDEX IF EXISTS idx_episode_created_by_user;
DROP INDEX IF EXISTS idx_media_migrated_by_user;
DROP INDEX IF EXISTS idx_media_last_scraped_by_user;
DROP INDEX IF EXISTS idx_media_last_refreshed_by_user;
DROP INDEX IF EXISTS idx_media_blocked_by_user;
DROP INDEX IF EXISTS idx_media_lower_title;

CREATE INDEX IF NOT EXISTS ix_media_updated_at
    ON public.media (updated_at);

CREATE INDEX IF NOT EXISTS ix_stream_updated_at
    ON public.stream (updated_at);

DROP INDEX IF EXISTS idx_media_visible;
CREATE INDEX IF NOT EXISTS idx_media_visible
    ON media (last_stream_added DESC NULLS LAST, id DESC)
    WHERE NOT (is_blocked OR is_keyword_blocked OR poster_nsfw_flagged);

ALTER TABLE media DROP COLUMN IF EXISTS keyword_block_override;

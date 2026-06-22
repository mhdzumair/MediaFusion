-- Per-media override: admin can suppress keyword blocking for a specific row
-- (e.g. when a keyword produces a false positive on a legitimate title/description).
ALTER TABLE media ADD COLUMN IF NOT EXISTS keyword_block_override BOOLEAN NOT NULL DEFAULT FALSE;

-- Recreate the visible-rows partial index to respect the override.
DROP INDEX IF EXISTS idx_media_visible;
CREATE INDEX IF NOT EXISTS idx_media_visible
    ON media (last_stream_added DESC NULLS LAST, id DESC)
    WHERE NOT (is_blocked OR (is_keyword_blocked AND NOT keyword_block_override) OR poster_nsfw_flagged);

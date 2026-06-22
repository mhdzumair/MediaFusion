-- Add per-poster NSFW classification columns to media.
--
-- Fields are intentionally separate from the sticky `adult` flag:
--   • poster_nsfw_score    — raw classifier output (NULL = not yet classified)
--   • poster_nsfw_flagged  — score ≥ threshold AND not admin-cleared
--   • poster_nsfw_reviewed — admin has acted; suppresses auto-recompute
--   • poster_nsfw_model_ver — model version that produced the score; drives re-scan
--
-- Catalog exclusion is applied via "AND m.poster_nsfw_flagged = false" when
-- POSTER_NSFW_ENABLED=true.  Flagged rows are never permanently hidden —
-- admin can clear poster_nsfw_flagged and set poster_nsfw_reviewed = true.

ALTER TABLE media
    ADD COLUMN IF NOT EXISTS poster_nsfw_score      real,
    ADD COLUMN IF NOT EXISTS poster_nsfw_flagged    boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS poster_nsfw_reviewed   boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS poster_nsfw_model_ver  text;

-- Partial index mirrors idx_media_keyword_blocked; makes flagged-media admin
-- queries and catalog exclusion nearly free.
CREATE INDEX IF NOT EXISTS idx_media_poster_nsfw_flagged
    ON media (poster_nsfw_flagged) WHERE poster_nsfw_flagged = true;

-- Index to efficiently find rows that still need classification (backfill job).
CREATE INDEX IF NOT EXISTS idx_media_poster_nsfw_unscanned
    ON media (id)
    WHERE poster_nsfw_model_ver IS NULL AND poster_nsfw_reviewed = false;

-- Register the NSFW backfill scan as a weekly cron job (disabled by default).
-- Enable via admin UI or: UPDATE cron_jobs SET enabled = true WHERE name = 'poster_nsfw_scan';
-- Set POSTER_NSFW_ENABLED=true and provide the model before enabling.
INSERT INTO cron_jobs (name, schedule, queue, payload, enabled)
VALUES ('poster_nsfw_scan', '0 3 * * 0', 'poster_nsfw_scan', '{}', false)
ON CONFLICT (name) DO NOTHING;

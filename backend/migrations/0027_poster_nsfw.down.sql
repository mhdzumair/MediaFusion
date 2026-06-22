-- Revert NSFW poster classification columns.
DELETE FROM cron_jobs WHERE name = 'poster_nsfw_scan';

DROP INDEX IF EXISTS idx_media_poster_nsfw_unscanned;
DROP INDEX IF EXISTS idx_media_poster_nsfw_flagged;

ALTER TABLE media
    DROP COLUMN IF EXISTS poster_nsfw_model_ver,
    DROP COLUMN IF EXISTS poster_nsfw_reviewed,
    DROP COLUMN IF EXISTS poster_nsfw_flagged,
    DROP COLUMN IF EXISTS poster_nsfw_score;

-- Partial index covering all *visible* media rows (not blocked, not keyword-blocked, not NSFW).
-- Supports the restriction predicate  NOT (is_blocked OR is_keyword_blocked OR poster_nsfw_flagged)
-- used by every catalog/browse query after migration 0028.
--
-- Keyed on (last_stream_added DESC NULLS LAST, id DESC) to match the default "latest" sort order,
-- so the planner can use an index-only scan for paginated catalog browse without re-evaluating the
-- three boolean columns per row.  The partial WHERE clause is the important part; the key columns
-- let Postgres avoid a sequential re-sort for the most common query shape.

CREATE INDEX IF NOT EXISTS idx_media_visible
    ON media (last_stream_added DESC NULLS LAST, id DESC)
    WHERE NOT (is_blocked OR is_keyword_blocked OR poster_nsfw_flagged);

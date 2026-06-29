-- Backfill stream_media_link rows for series torrents that were stored with only
-- file_media_link entries (bug: the code never wrote stream_media_link for
-- series when per-episode file rows were present, leaving total_streams = 0).

INSERT INTO stream_media_link (stream_id, media_id, is_primary, is_verified, created_at)
SELECT DISTINCT sf.stream_id, fml.media_id, false, false, NOW()
FROM file_media_link fml
JOIN stream_file sf ON sf.id = fml.file_id
WHERE NOT EXISTS (
    SELECT 1 FROM stream_media_link sml
    WHERE sml.stream_id = sf.stream_id AND sml.media_id = fml.media_id
);

-- Update total_streams for affected media rows (only those still at 0).
UPDATE media m
SET total_streams = subq.cnt
FROM (
    SELECT media_id, COUNT(*) AS cnt
    FROM stream_media_link
    GROUP BY media_id
) subq
WHERE m.id = subq.media_id
  AND m.total_streams = 0;

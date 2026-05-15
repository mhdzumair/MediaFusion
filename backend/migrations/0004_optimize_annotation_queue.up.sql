-- stream_media_link already has idx_stream_media_media (media_id) and
-- idx_stream_media_link_media_stream (media_id, stream_id) — no new index needed.
-- The Rust annotation queue handler uses those existing indexes.
SELECT 1;

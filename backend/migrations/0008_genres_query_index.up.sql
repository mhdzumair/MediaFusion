-- no-transaction
-- Covering index for the genres-by-media-type query.
-- EXISTS subquery joins media_genre_link on genre_id then looks up media_id;
-- this index lets that inner scan run without touching the heap.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_genre_link_genre_media
    ON media_genre_link(genre_id, media_id);

-- no-transaction
-- GIN trigram index on stream.name enables ILIKE '%x%' queries to use the index
-- instead of seq-scanning the full stream table (used by Torznab search).
-- Requires pg_trgm extension (enabled in startup.sh / docker init).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_stream_name_trgm
    ON stream USING gin (name gin_trgm_ops);

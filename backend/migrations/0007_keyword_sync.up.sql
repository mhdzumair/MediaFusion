-- Track whether each keyword/phrase was seeded from the bundled file or added manually.
ALTER TABLE public.keyword_filters
    ADD COLUMN source character varying NOT NULL DEFAULT 'admin';

ALTER TABLE public.keyword_whitelist
    ADD COLUMN source character varying NOT NULL DEFAULT 'admin';

-- All rows that exist before this migration were seeded from the file.
UPDATE public.keyword_filters  SET source = 'file';
UPDATE public.keyword_whitelist SET source = 'file';

-- Store the SHA-256 of the keywords file so we can skip syncs when nothing changed.
CREATE TABLE public.keyword_sync_state (
    id            character varying PRIMARY KEY,
    file_hash     character varying NOT NULL,
    synced_at     timestamp with time zone NOT NULL DEFAULT NOW()
);

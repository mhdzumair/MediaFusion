ALTER TABLE imdb_import_state
    ADD COLUMN IF NOT EXISTS rows_merged bigint;

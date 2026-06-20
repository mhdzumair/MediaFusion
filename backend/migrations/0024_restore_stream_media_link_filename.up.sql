-- Restore filename column dropped in 0019 for backward compatibility with Python v5.x.
-- The Rust backend does not use this column but Python clients still write to it.
ALTER TABLE stream_media_link ADD COLUMN IF NOT EXISTS filename character varying;

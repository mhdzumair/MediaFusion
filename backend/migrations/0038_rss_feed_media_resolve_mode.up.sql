ALTER TABLE rss_feed
    ADD COLUMN IF NOT EXISTS media_resolve_mode VARCHAR(20) NOT NULL DEFAULT 'strict';

-- Add per-feed credential params that get forwarded to item download links.
-- Useful for feeds where the API credentials are query params (e.g. ?username=&passkey=)
-- that also need to be passed when fetching individual .torrent file links from the same site.
ALTER TABLE rss_feed ADD COLUMN IF NOT EXISTS credential_params JSONB;

-- User-submitted RSS feeds require admin approval before they are scraped.
-- Admin-created feeds (is_public = false, owned by admin user) are auto-approved.
ALTER TABLE rss_feed ADD COLUMN IF NOT EXISTS is_approved BOOLEAN NOT NULL DEFAULT false;

-- Approve all existing feeds that are owned by admin users (retroactive migration).
UPDATE rss_feed
SET is_approved = true
WHERE user_id IN (SELECT id FROM users WHERE role = 'ADMIN'::userrole);

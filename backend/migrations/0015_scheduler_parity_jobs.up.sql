INSERT INTO cron_jobs (name, schedule, queue, payload, enabled) VALUES
    ('pending_moderation_reminder', '0 */6 * * *', 'pending_moderation_reminder', '{}', false),
    ('spider_arab_torrents',        '0 0 * * *',   'spider_arab_torrents',        '{}', true)
ON CONFLICT (name) DO NOTHING;

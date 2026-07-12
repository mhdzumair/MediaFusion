INSERT INTO cron_jobs (name, schedule, queue, payload, enabled) VALUES
    ('spider_fighting_feeds', '*/20 * * * *', 'spider_fighting_feeds', '{}', true),
    ('spider_movierulz', '0 */2 * * *', 'spider_movierulz', '{}', true)
ON CONFLICT (name) DO NOTHING;

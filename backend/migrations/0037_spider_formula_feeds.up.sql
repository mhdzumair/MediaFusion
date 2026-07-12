INSERT INTO cron_jobs (name, schedule, queue, payload, enabled) VALUES
    ('spider_formula_feeds', '*/15 * * * *', 'spider_formula_feeds', '{}', true)
ON CONFLICT (name) DO NOTHING;

INSERT INTO cron_jobs (name, schedule, queue, payload, enabled) VALUES
    ('daily_digest', '0 8 * * *', 'daily_digest', '{}', true)
ON CONFLICT (name) DO NOTHING;

INSERT INTO scheduled_job (name, crontab, job_type, args, enabled) VALUES
    ('spider_registry_ilcorsaronero', '0 */6 * * *',  'spider_registry_crawl', '{"indexer":"ilcorsaronero"}', true),
    ('spider_registry_comando',        '15 */6 * * *', 'spider_registry_crawl', '{"indexer":"comando"}',        true),
    ('spider_registry_bludv',          '30 */6 * * *', 'spider_registry_crawl', '{"indexer":"bludv"}',          true)
ON CONFLICT (name) DO NOTHING;

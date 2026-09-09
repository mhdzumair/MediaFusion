DELETE FROM cron_jobs
WHERE name IN (
    'spider_registry_ilcorsaronero',
    'spider_registry_comando',
    'spider_registry_bludv'
);

DELETE FROM scheduled_job
WHERE name IN (
    'spider_registry_ilcorsaronero',
    'spider_registry_comando',
    'spider_registry_bludv'
);

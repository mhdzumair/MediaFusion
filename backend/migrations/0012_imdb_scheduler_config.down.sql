UPDATE cron_jobs
SET payload = '{}'::jsonb
WHERE name = 'imdb_dataset_import';

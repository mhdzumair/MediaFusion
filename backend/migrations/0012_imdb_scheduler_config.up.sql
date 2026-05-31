-- Store default IMDb import options in cron_jobs.payload for admin UI management.
UPDATE cron_jobs
SET payload = '{
  "datasets": ["basics", "names", "ratings", "akas", "episode", "crew", "principals"],
  "include_adult": false
}'::jsonb
WHERE name = 'imdb_dataset_import'
  AND payload = '{}'::jsonb;

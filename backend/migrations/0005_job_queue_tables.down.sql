DROP TRIGGER IF EXISTS jobs_notify_new_tg ON jobs;
DROP FUNCTION IF EXISTS jobs_notify_new();
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS job_events;
DROP TABLE IF EXISTS cron_jobs;

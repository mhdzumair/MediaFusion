DELETE FROM cron_jobs WHERE name = 'imdb_dataset_import';

DROP TABLE IF EXISTS imdb_stage_names;
DROP TABLE IF EXISTS imdb_stage_principals;
DROP TABLE IF EXISTS imdb_stage_episode;
DROP TABLE IF EXISTS imdb_stage_crew;
DROP TABLE IF EXISTS imdb_stage_akas;
DROP TABLE IF EXISTS imdb_stage_ratings;
DROP TABLE IF EXISTS imdb_stage_basics;
DROP TABLE IF EXISTS imdb_import_state;

-- Provider seed rows are intentionally left in place (shared with Python runtime).

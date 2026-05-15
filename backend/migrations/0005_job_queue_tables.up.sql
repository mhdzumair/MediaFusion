CREATE TABLE IF NOT EXISTS cron_jobs (
    name             TEXT        PRIMARY KEY,
    schedule         TEXT        NOT NULL,
    queue            TEXT        NOT NULL,
    payload          JSONB       NOT NULL DEFAULT '{}',
    enabled          BOOLEAN     NOT NULL DEFAULT true,
    last_enqueued_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS job_events (
    id     BIGSERIAL   PRIMARY KEY,
    job_id BIGINT      NOT NULL,
    event  TEXT        NOT NULL,
    detail JSONB,
    at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_events_job_id_idx ON job_events (job_id);

CREATE TABLE IF NOT EXISTS jobs (
    id               BIGSERIAL   PRIMARY KEY,
    queue            TEXT        NOT NULL,
    payload          JSONB       NOT NULL DEFAULT '{}',
    status           TEXT        NOT NULL DEFAULT 'pending',
    priority         SMALLINT    NOT NULL DEFAULT 100,
    attempts         INT         NOT NULL DEFAULT 0,
    max_attempts     INT         NOT NULL DEFAULT 5,
    scheduled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    worker_id        TEXT,
    last_error       TEXT,
    cancel_requested BOOLEAN     NOT NULL DEFAULT false,
    dedupe_key       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_dedupe_key_uq UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS ix_jobs_queue            ON jobs (queue);
CREATE INDEX IF NOT EXISTS jobs_claim_idx           ON jobs (queue, priority, scheduled_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS jobs_created_at_idx      ON jobs (created_at);
CREATE INDEX IF NOT EXISTS jobs_running_idx         ON jobs (worker_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS jobs_status_finished_idx ON jobs (status, finished_at);

CREATE OR REPLACE FUNCTION jobs_notify_new() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'pending' THEN
        PERFORM pg_notify('jobs_new_' || NEW.queue, NEW.id::text);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS jobs_notify_new_tg ON jobs;
CREATE TRIGGER jobs_notify_new_tg
    AFTER INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION jobs_notify_new();

INSERT INTO cron_jobs (name, schedule, queue, payload, enabled) VALUES
    -- Non-Scrapy background tasks
    ('background_search',      '*/3 * * * *',  'background_search', '{}', true),
    ('prowlarr_feed',          '0 */3 * * *',  'prowlarr_feed',     '{}', true),
    ('jackett_feed',           '0 */3 * * *',  'jackett_feed',      '{}', true),
    ('rss_feed',               '0 */3 * * *',  'rss_feed',          '{}', true),
    ('dmm_hashlist',           '0 * * * *',    'dmm_hashlist',      '{}', true),
    ('youtube_bg',             '20 */6 * * *', 'youtube_bg',        '{}', false),
    ('acestream_bg',           '40 */6 * * *', 'acestream_bg',      '{}', true),
    ('telegram_bg',            '10 */6 * * *', 'telegram_bg',       '{}', false),
    ('validate_tv',            '0 0 * * 4',    'validate_tv',       '{}', true),
    ('update_seeders',         '0 0 * * 3',    'update_seeders',    '{}', false),
    ('update_tv_posters',      '0 2 * * *',    'update_tv_posters', '{}', true),
    ('discover_prewarm',       '0 4 * * *',    'discover_prewarm',  '{}', true),
    ('integration_syncs',      '0 */6 * * *',  'integration_syncs', '{}', true),
    ('cleanup_scraper_task',   '0 * * * *',    'cleanup', '{"task":"scraper_task"}', true),
    ('cleanup_cache',          '0 0 * * *',    'cleanup', '{"task":"cache"}',        true),
    -- Spider handlers (direct, non-registry)
    ('spider_tamilmv',         '0 */3 * * *',  'spider_tamilmv',        '{}', true),
    ('spider_tamil_blasters',  '0 */6 * * *',  'spider_tamil_blasters', '{}', true),
    ('spider_formula_ext',     '*/30 * * * *', 'spider_formula_ext',    '{}', true),
    ('spider_motogp_ext',      '0 5 * * *',    'spider_motogp_ext',     '{}', true),
    ('spider_wwe_ext',         '10 */3 * * *', 'spider_wwe_ext',        '{}', true),
    ('spider_ufc_ext',         '30 */3 * * *', 'spider_ufc_ext',        '{}', true),
    ('spider_movies_tv_ext',   '0 * * * *',    'spider_movies_tv_ext',  '{}', true),
    ('spider_sport_video',     '*/20 * * * *', 'spider_sport_video',    '{}', true),
    ('spider_eztv_rss',        '0 */2 * * *',  'spider_eztv_rss',       '{}', true),
    -- Registry-driven spiders
    ('spider_registry_nyaa',          '15 */3 * * *', 'spider_registry_crawl', '{"indexer":"nyaa"}',          true),
    ('spider_registry_animetosho',    '30 */4 * * *', 'spider_registry_crawl', '{"indexer":"animetosho"}',    true),
    ('spider_registry_subsplease',    '45 */4 * * *', 'spider_registry_crawl', '{"indexer":"subsplease"}',    true),
    ('spider_registry_animepahe',     '0 */6 * * *',  'spider_registry_crawl', '{"indexer":"animepahe"}',     false),
    ('spider_registry_bt52',          '30 */6 * * *', 'spider_registry_crawl', '{"indexer":"bt52"}',          false),
    ('spider_registry_uindex',        '0 */4 * * *',  'spider_registry_crawl', '{"indexer":"uindex"}',        false),
    ('spider_registry_x1337',         '0 */6 * * *',  'spider_registry_crawl', '{"indexer":"x1337"}',         false),
    ('spider_registry_thepiratebay',  '30 */6 * * *', 'spider_registry_crawl', '{"indexer":"thepiratebay"}',  false),
    ('spider_registry_rutor',         '45 */6 * * *', 'spider_registry_crawl', '{"indexer":"rutor"}',         false),
    ('spider_registry_limetorrents',  '0 */8 * * *',  'spider_registry_crawl', '{"indexer":"limetorrents"}',  false),
    ('spider_registry_yts',           '0 */12 * * *', 'spider_registry_crawl', '{"indexer":"yts"}',           false),
    ('spider_registry_bt4g',          '15 */8 * * *', 'spider_registry_crawl', '{"indexer":"bt4g"}',          false)
ON CONFLICT (name) DO NOTHING;

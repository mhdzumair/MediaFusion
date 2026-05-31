-- IMDb bulk import infrastructure (additive only — no ALTER on existing tables).

-- ── Provider seeds (idempotent) ─────────────────────────────────────────────

INSERT INTO metadata_provider (name, display_name, is_external, is_active, priority, default_priority, created_at)
VALUES
    ('imdb', 'IMDb', true, true, 10, 10, now()),
    ('tvdb', 'TVDB', true, true, 15, 15, now()),
    ('tmdb', 'TMDB', true, true, 20, 20, now())
ON CONFLICT (name) DO NOTHING;

INSERT INTO rating_provider (name, display_name, max_rating, is_percentage, is_active, display_order)
VALUES ('imdb', 'IMDb', 10, false, true, 10)
ON CONFLICT (name) DO NOTHING;

-- ── Import state (conditional GET + observability) ──────────────────────────

CREATE TABLE IF NOT EXISTS imdb_import_state (
    dataset       text PRIMARY KEY,
    etag          text,
    last_modified text,
    last_run_at   timestamptz,
    rows_loaded   bigint
);

-- ── UNLOGGED staging tables (all text; column order matches IMDb TSV headers) ─

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_basics (
    tconst          text,
    title_type      text,
    primary_title   text,
    original_title  text,
    is_adult        text,
    start_year      text,
    end_year        text,
    runtime_minutes text,
    genres          text
);

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_ratings (
    tconst          text,
    average_rating  text,
    num_votes       text
);

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_akas (
    title_id            text,
    ordering            text,
    title               text,
    region              text,
    language            text,
    types               text,
    attributes          text,
    is_original_title   text
);

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_crew (
    tconst      text,
    directors   text,
    writers     text
);

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_episode (
    tconst          text,
    parent_tconst   text,
    season_number   text,
    episode_number  text
);

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_principals (
    tconst      text,
    ordering    text,
    nconst      text,
    category    text,
    job         text,
    characters  text
);

CREATE UNLOGGED TABLE IF NOT EXISTS imdb_stage_names (
    nconst              text,
    primary_name        text,
    birth_year          text,
    death_year          text,
    primary_profession  text,
    known_for_titles    text
);

CREATE INDEX IF NOT EXISTS imdb_stage_basics_tconst_idx
    ON imdb_stage_basics (tconst);
CREATE INDEX IF NOT EXISTS imdb_stage_ratings_tconst_idx
    ON imdb_stage_ratings (tconst);
CREATE INDEX IF NOT EXISTS imdb_stage_akas_title_id_idx
    ON imdb_stage_akas (title_id);
CREATE INDEX IF NOT EXISTS imdb_stage_crew_tconst_idx
    ON imdb_stage_crew (tconst);
CREATE INDEX IF NOT EXISTS imdb_stage_episode_parent_idx
    ON imdb_stage_episode (parent_tconst);
CREATE INDEX IF NOT EXISTS imdb_stage_principals_tconst_idx
    ON imdb_stage_principals (tconst);
CREATE INDEX IF NOT EXISTS imdb_stage_principals_nconst_idx
    ON imdb_stage_principals (nconst);
CREATE INDEX IF NOT EXISTS imdb_stage_names_nconst_idx
    ON imdb_stage_names (nconst);

-- Weekly refresh (disabled by default — enable via admin UI or UPDATE cron_jobs)
INSERT INTO cron_jobs (name, schedule, queue, payload, enabled)
VALUES ('imdb_dataset_import', '0 4 * * 0', 'imdb_dataset_import', '{}', false)
ON CONFLICT (name) DO NOTHING;

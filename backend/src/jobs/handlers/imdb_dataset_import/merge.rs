use sqlx::PgPool;

use crate::jobs::error::JobError;

pub struct ProviderIds {
    pub metadata_provider_id: i32,
    pub rating_provider_id: i32,
    pub imdb_priority: i32,
}

pub async fn load_provider_ids(pool: &PgPool) -> Result<ProviderIds, JobError> {
    let meta_id: Option<i32> =
        sqlx::query_scalar("SELECT id FROM metadata_provider WHERE lower(name) = 'imdb' LIMIT 1")
            .fetch_optional(pool)
            .await?;

    let rating_id: Option<i32> =
        sqlx::query_scalar("SELECT id FROM rating_provider WHERE lower(name) = 'imdb' LIMIT 1")
            .fetch_optional(pool)
            .await?;

    let priority: Option<i32> = sqlx::query_scalar(
        "SELECT priority FROM metadata_provider WHERE lower(name) = 'imdb' LIMIT 1",
    )
    .fetch_optional(pool)
    .await?;

    let metadata_provider_id = meta_id.ok_or_else(|| {
        JobError::other("metadata_provider 'imdb' not found — run migration 0011")
    })?;
    let rating_provider_id = rating_id
        .ok_or_else(|| JobError::other("rating_provider 'imdb' not found — run migration 0011"))?;

    Ok(ProviderIds {
        metadata_provider_id,
        rating_provider_id,
        imdb_priority: priority.unwrap_or(10),
    })
}

pub async fn merge_basics(
    pool: &PgPool,
    providers: &ProviderIds,
    include_adult: bool,
) -> Result<u64, JobError> {
    let mut tx = pool.begin().await?;

    // ── New IMDb-only media + external IDs ───────────────────────────────────
    let inserted = sqlx::query(
        r#"
        WITH filtered AS (
            SELECT *
            FROM imdb_stage_basics s
            WHERE lower(s.title_type) IN (
                'movie', 'tvmovie', 'tvseries', 'tvminiseries', 'tvspecial', 'short', 'video'
            )
            AND lower(s.title_type) <> 'tvepisode'
            AND ($1::bool OR s.is_adult = '0')
            AND s.tconst IS NOT NULL AND s.tconst <> ''
            AND s.primary_title IS NOT NULL AND s.primary_title <> '' AND s.primary_title <> '\N'
            AND NOT EXISTS (
                SELECT 1 FROM media_external_id mei
                WHERE mei.provider = 'imdb' AND mei.external_id = s.tconst
            )
        ),
        allocated AS (
            SELECT
                f.*,
                nextval('media_id_seq') AS new_media_id
            FROM filtered f
        ),
        ins_media AS (
            INSERT INTO media (
                id, type, title, original_title, year, runtime_minutes, end_date,
                adult, nudity_status, is_blocked, is_public, is_user_created,
                total_streams, primary_provider_id, created_at
            )
            SELECT
                a.new_media_id,
                CASE
                    WHEN lower(a.title_type) IN ('tvseries', 'tvminiseries') THEN 'SERIES'::mediatype
                    ELSE 'MOVIE'::mediatype
                END,
                a.primary_title,
                NULLIF(a.original_title, '\N'),
                NULLIF(a.start_year, '\N')::integer,
                NULLIF(a.runtime_minutes, '\N')::integer,
                CASE
                    WHEN NULLIF(a.end_year, '\N') IS NOT NULL
                    THEN make_date(NULLIF(a.end_year, '\N')::integer, 12, 31)
                    ELSE NULL
                END,
                (a.is_adult = '1'),
                'NONE'::nuditystatus,
                false, true, false, 0,
                $2,
                now()
            FROM allocated a
            RETURNING id
        ),
        ins_ext AS (
            INSERT INTO media_external_id (media_id, provider, external_id, created_at)
            SELECT a.new_media_id, 'imdb', a.tconst, now()
            FROM allocated a
            ON CONFLICT (provider, external_id) DO NOTHING
            RETURNING media_id
        ),
        ins_series AS (
            INSERT INTO series_metadata (media_id, total_seasons, total_episodes, created_at)
            SELECT a.new_media_id, 0, 0, now()
            FROM allocated a
            WHERE lower(a.title_type) IN ('tvseries', 'tvminiseries')
            ON CONFLICT (media_id) DO NOTHING
            RETURNING media_id
        ),
        ins_movie AS (
            INSERT INTO movie_metadata (media_id, created_at)
            SELECT a.new_media_id, now()
            FROM allocated a
            WHERE lower(a.title_type) NOT IN ('tvseries', 'tvminiseries')
            ON CONFLICT (media_id) DO NOTHING
            RETURNING media_id
        )
        SELECT COUNT(*)::bigint FROM ins_media
        "#,
    )
    .bind(include_adult)
    .bind(providers.metadata_provider_id)
    .execute(&mut *tx)
    .await?
    .rows_affected();

    // ── Gap-fill / priority overwrite on existing media ─────────────────────
    sqlx::query(
        r#"
        UPDATE media SET
            title = CASE
                WHEN $2 < COALESCE((
                    SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                ), 999) AND s.primary_title IS NOT NULL
                     AND media.title IS DISTINCT FROM s.primary_title
                THEN s.primary_title
                WHEN media.title IS NULL THEN s.primary_title
                ELSE media.title END,
            original_title = CASE
                WHEN $2 < COALESCE((
                    SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                ), 999)
                     AND NULLIF(s.original_title, '\N') IS NOT NULL
                     AND media.original_title IS DISTINCT FROM NULLIF(s.original_title, '\N')
                THEN NULLIF(s.original_title, '\N')
                WHEN media.original_title IS NULL THEN NULLIF(s.original_title, '\N')
                ELSE media.original_title END,
            year = CASE
                WHEN $2 < COALESCE((
                    SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                ), 999)
                     AND NULLIF(s.start_year, '\N') IS NOT NULL
                     AND media.year IS DISTINCT FROM NULLIF(s.start_year, '\N')::integer
                THEN NULLIF(s.start_year, '\N')::integer
                WHEN media.year IS NULL THEN NULLIF(s.start_year, '\N')::integer
                ELSE media.year END,
            runtime_minutes = CASE
                WHEN $2 < COALESCE((
                    SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                ), 999)
                     AND NULLIF(s.runtime_minutes, '\N') IS NOT NULL
                     AND media.runtime_minutes IS DISTINCT FROM NULLIF(s.runtime_minutes, '\N')::integer
                THEN NULLIF(s.runtime_minutes, '\N')::integer
                WHEN media.runtime_minutes IS NULL THEN NULLIF(s.runtime_minutes, '\N')::integer
                ELSE media.runtime_minutes END,
            end_date = CASE
                WHEN $2 < COALESCE((
                    SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                ), 999)
                     AND NULLIF(s.end_year, '\N') IS NOT NULL
                     AND media.end_date IS DISTINCT FROM make_date(NULLIF(s.end_year, '\N')::integer, 12, 31)
                THEN make_date(NULLIF(s.end_year, '\N')::integer, 12, 31)
                WHEN media.end_date IS NULL AND NULLIF(s.end_year, '\N') IS NOT NULL
                THEN make_date(NULLIF(s.end_year, '\N')::integer, 12, 31)
                ELSE media.end_date END,
            adult = CASE
                WHEN $2 < COALESCE((
                    SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                ), 999) AND media.adult IS DISTINCT FROM (s.is_adult = '1')
                THEN (s.is_adult = '1')
                ELSE media.adult END,
            updated_at = now()
        FROM imdb_stage_basics s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        WHERE media.id = mei.media_id
          AND media.is_user_created = false
          AND media.user_original_title IS NULL
          AND lower(s.title_type) IN (
              'movie', 'tvmovie', 'tvseries', 'tvminiseries', 'tvspecial', 'short', 'video'
          )
          AND ($1::bool OR s.is_adult = '0')
        "#,
    )
    .bind(include_adult)
    .bind(providers.imdb_priority)
    .execute(&mut *tx)
    .await?;

    // Ensure series_metadata for existing series matched via IMDb id.
    sqlx::query(
        r#"
        INSERT INTO series_metadata (media_id, total_seasons, total_episodes, created_at)
        SELECT DISTINCT mei.media_id, 0, 0, now()
        FROM imdb_stage_basics s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        JOIN media m ON m.id = mei.media_id AND m.type = 'SERIES'::mediatype
        WHERE lower(s.title_type) IN ('tvseries', 'tvminiseries')
        ON CONFLICT (media_id) DO NOTHING
        "#,
    )
    .execute(&mut *tx)
    .await?;

    // ── Genres (additive) ───────────────────────────────────────────────────
    sqlx::query(
        r#"
        INSERT INTO genre (name)
        SELECT DISTINCT trim(g.genre_name)
        FROM imdb_stage_basics s
        CROSS JOIN LATERAL unnest(string_to_array(s.genres, ',')) AS g(genre_name)
        WHERE s.genres IS NOT NULL AND s.genres <> '\N' AND trim(g.genre_name) <> ''
        ON CONFLICT (name) DO NOTHING
        "#,
    )
    .execute(&mut *tx)
    .await?;

    sqlx::query(
        r#"
        INSERT INTO media_genre_link (media_id, genre_id)
        SELECT DISTINCT mei.media_id, g.id
        FROM imdb_stage_basics s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        CROSS JOIN LATERAL unnest(string_to_array(s.genres, ',')) AS gn(genre_name)
        JOIN genre g ON g.name = trim(gn.genre_name)
        WHERE s.genres IS NOT NULL AND s.genres <> '\N' AND trim(gn.genre_name) <> ''
        ON CONFLICT (media_id, genre_id) DO NOTHING
        "#,
    )
    .execute(&mut *tx)
    .await?;

    tx.commit().await?;
    Ok(inserted)
}

pub async fn merge_ratings(pool: &PgPool, providers: &ProviderIds) -> Result<u64, JobError> {
    let result = sqlx::query(
        r#"
        INSERT INTO media_rating (
            media_id, rating_provider_id, rating, vote_count, rating_type,
            fetched_at, updated_at
        )
        SELECT
            mei.media_id,
            $1,
            NULLIF(s.average_rating, '\N')::double precision,
            NULLIF(s.num_votes, '\N')::integer,
            'user',
            now(), now()
        FROM imdb_stage_ratings s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        WHERE NULLIF(s.average_rating, '\N') IS NOT NULL
        ON CONFLICT (media_id, rating_provider_id, rating_type) DO UPDATE SET
            rating = EXCLUDED.rating,
            vote_count = EXCLUDED.vote_count,
            updated_at = now()
        WHERE media_rating.rating IS DISTINCT FROM EXCLUDED.rating
           OR media_rating.vote_count IS DISTINCT FROM EXCLUDED.vote_count
        "#,
    )
    .bind(providers.rating_provider_id)
    .execute(pool)
    .await?;

    Ok(result.rows_affected())
}

pub async fn merge_names(pool: &PgPool, providers: &ProviderIds) -> Result<u64, JobError> {
    let result = sqlx::query(
        r#"
        INSERT INTO person (
            imdb_id, name, birthday, deathday, known_for_department,
            provider_id, created_at
        )
        SELECT
            s.nconst,
            s.primary_name,
            CASE WHEN NULLIF(s.birth_year, '\N') IS NOT NULL
                 THEN make_date(NULLIF(s.birth_year, '\N')::integer, 1, 1) END,
            CASE WHEN NULLIF(s.death_year, '\N') IS NOT NULL
                 THEN make_date(NULLIF(s.death_year, '\N')::integer, 1, 1) END,
            NULLIF(split_part(s.primary_profession, ',', 1), '\N'),
            $1,
            now()
        FROM imdb_stage_names s
        WHERE s.nconst IS NOT NULL AND s.nconst <> '' AND s.nconst <> '\N'
          AND s.primary_name IS NOT NULL AND s.primary_name <> '' AND s.primary_name <> '\N'
        ON CONFLICT (imdb_id) DO UPDATE SET
            name = COALESCE(NULLIF(EXCLUDED.name, ''), person.name),
            birthday = COALESCE(EXCLUDED.birthday, person.birthday),
            deathday = COALESCE(EXCLUDED.deathday, person.deathday),
            known_for_department = COALESCE(EXCLUDED.known_for_department, person.known_for_department),
            provider_id = COALESCE(person.provider_id, EXCLUDED.provider_id),
            updated_at = now()
        WHERE person.name IS DISTINCT FROM COALESCE(NULLIF(EXCLUDED.name, ''), person.name)
           OR person.birthday IS DISTINCT FROM COALESCE(EXCLUDED.birthday, person.birthday)
           OR person.deathday IS DISTINCT FROM COALESCE(EXCLUDED.deathday, person.deathday)
        "#,
    )
    .bind(providers.metadata_provider_id)
    .execute(pool)
    .await?;

    Ok(result.rows_affected())
}

pub async fn merge_akas(pool: &PgPool) -> Result<u64, JobError> {
    let result = sqlx::query(
        r#"
        INSERT INTO aka_title (media_id, title, language_code)
        SELECT DISTINCT mei.media_id, s.title, NULLIF(s.language, '\N')
        FROM imdb_stage_akas s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.title_id
        WHERE s.title IS NOT NULL AND s.title <> '' AND s.title <> '\N'
          AND NOT EXISTS (
              SELECT 1 FROM media m
              WHERE m.id = mei.media_id AND lower(m.title) = lower(s.title)
          )
        ON CONFLICT (media_id, title) DO NOTHING
        "#,
    )
    .execute(pool)
    .await?;

    Ok(result.rows_affected())
}

pub async fn merge_episodes(pool: &PgPool, providers: &ProviderIds) -> Result<u64, JobError> {
    let mut tx = pool.begin().await?;

    // Ensure series_metadata exists for parent series.
    sqlx::query(
        r#"
        INSERT INTO series_metadata (media_id, total_seasons, total_episodes, created_at)
        SELECT DISTINCT mei.media_id, 0, 0, now()
        FROM imdb_stage_episode e
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = e.parent_tconst
        ON CONFLICT (media_id) DO NOTHING
        "#,
    )
    .execute(&mut *tx)
    .await?;

    // Upsert seasons.
    sqlx::query(
        r#"
        INSERT INTO season (series_id, season_number, name, episode_count, provider_id)
        SELECT DISTINCT
            sm.id,
            NULLIF(e.season_number, '\N')::integer,
            'Season ' || NULLIF(e.season_number, '\N'),
            0,
            $1
        FROM imdb_stage_episode e
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = e.parent_tconst
        JOIN series_metadata sm ON sm.media_id = mei.media_id
        WHERE NULLIF(e.season_number, '\N') IS NOT NULL
          AND NULLIF(e.episode_number, '\N') IS NOT NULL
        ON CONFLICT (series_id, season_number) DO NOTHING
        "#,
    )
    .bind(providers.metadata_provider_id)
    .execute(&mut *tx)
    .await?;

    let result = sqlx::query(
        r#"
        INSERT INTO episode (
            season_id, episode_number, title, imdb_id, provider_id,
            is_user_created, is_user_addition, created_at, updated_at
        )
        SELECT
            sn.id,
            NULLIF(e.episode_number, '\N')::integer,
            COALESCE(NULLIF(b.primary_title, '\N'), 'Episode ' || NULLIF(e.episode_number, '\N')),
            e.tconst,
            $1,
            false, false, now(), now()
        FROM imdb_stage_episode e
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = e.parent_tconst
        JOIN series_metadata sm ON sm.media_id = mei.media_id
        JOIN season sn ON sn.series_id = sm.id
            AND sn.season_number = NULLIF(e.season_number, '\N')::integer
        LEFT JOIN imdb_stage_basics b ON b.tconst = e.tconst
        WHERE NULLIF(e.episode_number, '\N') IS NOT NULL
          AND e.tconst IS NOT NULL AND e.tconst <> '\N'
        ON CONFLICT (season_id, episode_number) DO UPDATE SET
            title = CASE
                WHEN episode.is_user_created = false AND episode.is_user_addition = false
                     AND episode.title IS DISTINCT FROM EXCLUDED.title
                THEN EXCLUDED.title ELSE episode.title END,
            imdb_id = COALESCE(episode.imdb_id, EXCLUDED.imdb_id),
            updated_at = now()
        WHERE episode.is_user_created = false AND episode.is_user_addition = false
        "#,
    )
    .bind(providers.metadata_provider_id)
    .execute(&mut *tx)
    .await?;

    tx.commit().await?;
    Ok(result.rows_affected())
}

pub async fn merge_crew(pool: &PgPool) -> Result<u64, JobError> {
    let mut affected = 0u64;

    for (col, job, dept) in [
        ("directors", "Director", "Directing"),
        ("writers", "Writer", "Writing"),
    ] {
        let sql = format!(
            r#"
            INSERT INTO media_crew (media_id, person_id, department, job)
            SELECT DISTINCT mei.media_id, p.id, '{dept}', '{job}'
            FROM imdb_stage_crew c
            JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = c.tconst
            CROSS JOIN LATERAL unnest(string_to_array(NULLIF(c.{col}, '\N'), ',')) AS n(nconst)
            JOIN person p ON p.imdb_id = trim(n.nconst)
            WHERE trim(n.nconst) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM media_crew mc
                  WHERE mc.media_id = mei.media_id
                    AND mc.person_id = p.id
                    AND COALESCE(mc.job, '') = '{job}'
              )
            "#
        );
        affected += sqlx::query(&sql).execute(pool).await?.rows_affected();
    }

    Ok(affected)
}

pub async fn merge_principals(pool: &PgPool) -> Result<u64, JobError> {
    let mut affected = 0u64;

    // Cast: actor, actress, self
    let cast_result = sqlx::query(
        r#"
        INSERT INTO media_cast (media_id, person_id, "character", display_order)
        SELECT mei.media_id, p.id,
               NULLIF(s.characters, '\N'),
               COALESCE(NULLIF(s.ordering, '\N')::integer, 0)
        FROM imdb_stage_principals s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        JOIN person p ON p.imdb_id = s.nconst
        WHERE lower(s.category) IN ('actor', 'actress', 'self')
          AND s.nconst IS NOT NULL AND s.nconst <> '\N'
          AND NOT EXISTS (
              SELECT 1 FROM media_cast mc
              WHERE mc.media_id = mei.media_id
                AND mc.person_id = p.id
                AND COALESCE(mc."character", '') = COALESCE(NULLIF(s.characters, '\N'), '')
          )
        "#,
    )
    .execute(pool)
    .await?;
    affected += cast_result.rows_affected();

    // Crew from principals (director, writer, producer, etc.)
    let crew_result = sqlx::query(
        r#"
        INSERT INTO media_crew (media_id, person_id, department, job)
        SELECT mei.media_id, p.id,
               initcap(s.category),
               COALESCE(NULLIF(s.job, '\N'), initcap(s.category))
        FROM imdb_stage_principals s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        JOIN person p ON p.imdb_id = s.nconst
        WHERE lower(s.category) NOT IN ('actor', 'actress', 'self')
          AND s.nconst IS NOT NULL AND s.nconst <> '\N'
          AND NOT EXISTS (
              SELECT 1 FROM media_crew mc
              WHERE mc.media_id = mei.media_id
                AND mc.person_id = p.id
                AND COALESCE(mc.job, '') = COALESCE(NULLIF(s.job, '\N'), initcap(s.category))
          )
        "#,
    )
    .execute(pool)
    .await?;
    affected += crew_result.rows_affected();

    Ok(affected)
}

pub async fn merge_dataset(
    pool: &PgPool,
    key: &str,
    providers: &ProviderIds,
    include_adult: bool,
) -> Result<u64, JobError> {
    match key {
        "basics" => merge_basics(pool, providers, include_adult).await,
        "ratings" => merge_ratings(pool, providers).await,
        "names" => merge_names(pool, providers).await,
        "akas" => merge_akas(pool).await,
        "episode" => merge_episodes(pool, providers).await,
        "crew" => merge_crew(pool).await,
        "principals" => merge_principals(pool).await,
        other => Err(JobError::other(format!("unknown dataset merge: {other}"))),
    }
}

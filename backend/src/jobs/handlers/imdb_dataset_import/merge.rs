use fred::clients::Client as RedisClient;
use fred::prelude::{Expiration, KeysInterface};
use sqlx::{PgPool, Postgres, Transaction};
use tokio_util::sync::CancellationToken;
use tracing::info;

use super::types::{ImportStatus, STATUS_REDIS_KEY};
use crate::jobs::error::JobError;

const STATUS_TTL_SECS: i64 = 86_400;
const BASICS_INSERT_BATCH: i64 = 5_000;
const BASICS_UPDATE_BATCH: i64 = 2_000;
const BASICS_GENRE_BATCH: i64 = 5_000;
const NAMES_BATCH: i64 = 10_000;
const RATINGS_BATCH: i64 = 25_000;
const AKAS_BATCH: i64 = 25_000;
const EPISODE_BATCH: i64 = 10_000;
const CREW_BATCH: i64 = 10_000;
const PRINCIPALS_BATCH: i64 = 25_000;

pub struct ProviderIds {
    pub metadata_provider_id: i32,
    pub rating_provider_id: i32,
    pub imdb_priority: i32,
}

struct MergeReporter<'a> {
    redis: &'a RedisClient,
    started_at: &'a str,
    dataset: &'a str,
    rows_loaded: i64,
}

impl<'a> MergeReporter<'a> {
    async fn report(
        &self,
        step: &str,
        processed: i64,
        total: Option<i64>,
        merged: u64,
        message: Option<&str>,
    ) {
        let status = ImportStatus {
            phase: "merge".into(),
            dataset: Some(self.dataset.to_string()),
            merge_step: Some(step.into()),
            rows_loaded: Some(self.rows_loaded),
            rows_merged: Some(merged as i64),
            rows_processed: Some(processed),
            rows_total: total,
            started_at: self.started_at.to_string(),
            message: message.map(str::to_string),
        };
        if let Ok(json) = serde_json::to_string(&status) {
            let _: Result<(), _> = self
                .redis
                .set::<(), _, _>(
                    STATUS_REDIS_KEY,
                    json,
                    Some(Expiration::EX(STATUS_TTL_SECS)),
                    None,
                    false,
                )
                .await;
        }
    }
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

async fn begin_long_running_tx(pool: &PgPool) -> Result<Transaction<'_, Postgres>, JobError> {
    let mut tx = pool.begin().await?;
    sqlx::query("SET LOCAL statement_timeout = '0'")
        .execute(&mut *tx)
        .await?;
    sqlx::query("SET LOCAL idle_in_transaction_session_timeout = '0'")
        .execute(&mut *tx)
        .await?;
    Ok(tx)
}

fn check_cancel(cancel: &CancellationToken) -> Result<(), JobError> {
    if cancel.is_cancelled() {
        return Err(JobError::Cancelled);
    }
    Ok(())
}

pub async fn merge_basics(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    providers: &ProviderIds,
    include_adult: bool,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "basics",
        rows_loaded,
    };

    let update_total: i64 = sqlx::query_scalar(
        r#"
        SELECT COUNT(*)::bigint
        FROM imdb_stage_basics s
        JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
        JOIN media m ON m.id = mei.media_id
        WHERE m.is_user_created = false
          AND m.user_original_title IS NULL
          AND lower(s.title_type) IN (
              'movie', 'tvmovie', 'tvseries', 'tvminiseries', 'tvspecial', 'short', 'video'
          )
          AND ($1::bool OR s.is_adult = '0')
        "#,
    )
    .bind(include_adult)
    .fetch_one(pool)
    .await?;

    let mut total_inserted = 0u64;
    let mut last_tconst = String::new();

    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;
        let (batch_inserted, batch_max): (i64, Option<String>) = sqlx::query_as(
            r#"
            WITH candidates AS (
                SELECT *
                FROM imdb_stage_basics s
                WHERE lower(s.title_type) IN (
                    'movie', 'tvmovie', 'tvseries', 'tvminiseries', 'tvspecial', 'short', 'video'
                )
                AND lower(s.title_type) <> 'tvepisode'
                AND ($1::bool OR s.is_adult = '0')
                AND s.tconst IS NOT NULL AND s.tconst <> ''
                AND s.primary_title IS NOT NULL AND s.primary_title <> '' AND s.primary_title <> '\N'
                AND s.tconst > $3
                ORDER BY s.tconst
                LIMIT $4
            ),
            filtered AS (
                SELECT c.*
                FROM candidates c
                WHERE NOT EXISTS (
                    SELECT 1 FROM media_external_id mei
                    WHERE mei.provider = 'imdb' AND mei.external_id = c.tconst
                )
            ),
            allocated AS (
                SELECT f.*, nextval('media_id_seq') AS new_media_id
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
            SELECT
                COALESCE((SELECT COUNT(*)::bigint FROM ins_media), 0),
                (SELECT MAX(tconst) FROM candidates)
            "#,
        )
        .bind(include_adult)
        .bind(providers.metadata_provider_id)
        .bind(&last_tconst)
        .bind(BASICS_INSERT_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;

        let Some(max_t) = batch_max else {
            break;
        };

        total_inserted += batch_inserted as u64;
        last_tconst = max_t;

        if batch_inserted == 0 {
            continue;
        }

        reporter
            .report(
                "insert_new",
                total_inserted as i64,
                None,
                total_inserted,
                Some(&format!("last tconst {last_tconst}")),
            )
            .await;
    }

    info!(
        inserted = total_inserted,
        "basics merge: new media inserted"
    );

    let mut last_media_id: i32 = 0;
    let mut total_updated = 0u64;

    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;

        let batch_end: Option<i32> = sqlx::query_scalar::<_, Option<i32>>(
            r#"
            SELECT MAX(batch.media_id)
            FROM (
                SELECT mei.media_id
                FROM imdb_stage_basics s
                JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
                JOIN media m ON m.id = mei.media_id
                WHERE mei.media_id > $1
                  AND m.is_user_created = false
                  AND m.user_original_title IS NULL
                  AND lower(s.title_type) IN (
                      'movie', 'tvmovie', 'tvseries', 'tvminiseries', 'tvspecial', 'short', 'video'
                  )
                  AND ($2::bool OR s.is_adult = '0')
                ORDER BY mei.media_id
                LIMIT $3
            ) batch
            "#,
        )
        .bind(last_media_id)
        .bind(include_adult)
        .bind(BASICS_UPDATE_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        let Some(batch_end) = batch_end else {
            tx.rollback().await?;
            break;
        };

        let updated = sqlx::query(
            r#"
            UPDATE media SET
                title = CASE
                    WHEN $3 < COALESCE((
                        SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                    ), 999) AND s.primary_title IS NOT NULL
                         AND media.title IS DISTINCT FROM s.primary_title
                    THEN s.primary_title
                    WHEN media.title IS NULL THEN s.primary_title
                    ELSE media.title END,
                original_title = CASE
                    WHEN $3 < COALESCE((
                        SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                    ), 999)
                         AND NULLIF(s.original_title, '\N') IS NOT NULL
                         AND media.original_title IS DISTINCT FROM NULLIF(s.original_title, '\N')
                    THEN NULLIF(s.original_title, '\N')
                    WHEN media.original_title IS NULL THEN NULLIF(s.original_title, '\N')
                    ELSE media.original_title END,
                year = CASE
                    WHEN $3 < COALESCE((
                        SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                    ), 999)
                         AND NULLIF(s.start_year, '\N') IS NOT NULL
                         AND media.year IS DISTINCT FROM NULLIF(s.start_year, '\N')::integer
                    THEN NULLIF(s.start_year, '\N')::integer
                    WHEN media.year IS NULL THEN NULLIF(s.start_year, '\N')::integer
                    ELSE media.year END,
                runtime_minutes = CASE
                    WHEN $3 < COALESCE((
                        SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                    ), 999)
                         AND NULLIF(s.runtime_minutes, '\N') IS NOT NULL
                         AND media.runtime_minutes IS DISTINCT FROM NULLIF(s.runtime_minutes, '\N')::integer
                    THEN NULLIF(s.runtime_minutes, '\N')::integer
                    WHEN media.runtime_minutes IS NULL THEN NULLIF(s.runtime_minutes, '\N')::integer
                    ELSE media.runtime_minutes END,
                end_date = CASE
                    WHEN $3 < COALESCE((
                        SELECT mp.priority FROM metadata_provider mp WHERE mp.id = media.primary_provider_id
                    ), 999)
                         AND NULLIF(s.end_year, '\N') IS NOT NULL
                         AND media.end_date IS DISTINCT FROM make_date(NULLIF(s.end_year, '\N')::integer, 12, 31)
                    THEN make_date(NULLIF(s.end_year, '\N')::integer, 12, 31)
                    WHEN media.end_date IS NULL AND NULLIF(s.end_year, '\N') IS NOT NULL
                    THEN make_date(NULLIF(s.end_year, '\N')::integer, 12, 31)
                    ELSE media.end_date END,
                adult = CASE
                    WHEN $3 < COALESCE((
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
              AND ($2::bool OR s.is_adult = '0')
              AND mei.media_id > $1
              AND mei.media_id <= $4
            "#,
        )
        .bind(last_media_id)
        .bind(include_adult)
        .bind(providers.imdb_priority)
        .bind(batch_end)
        .execute(&mut *tx)
        .await?
        .rows_affected();

        tx.commit().await?;

        total_updated += updated;
        last_media_id = batch_end;

        reporter
            .report(
                "update_existing",
                last_media_id as i64,
                Some(update_total),
                total_inserted + total_updated,
                Some(&format!("updated {updated} rows")),
            )
            .await;
    }

    info!(
        updated = total_updated,
        "basics merge: existing media updated"
    );

    {
        let mut tx = begin_long_running_tx(pool).await?;
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
        tx.commit().await?;
    }

    reporter
        .report(
            "genres",
            0,
            Some(update_total),
            total_inserted + total_updated,
            Some("syncing genres"),
        )
        .await;

    {
        let mut tx = begin_long_running_tx(pool).await?;
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
        tx.commit().await?;
    }

    last_media_id = 0;
    let mut total_genres = 0u64;
    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;

        let batch_end: Option<i32> = sqlx::query_scalar::<_, Option<i32>>(
            r#"
            SELECT MAX(batch.media_id)
            FROM (
                SELECT DISTINCT mei.media_id
                FROM imdb_stage_basics s
                JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
                WHERE s.genres IS NOT NULL AND s.genres <> '\N'
                  AND mei.media_id > $1
                ORDER BY mei.media_id
                LIMIT $2
            ) batch
            "#,
        )
        .bind(last_media_id)
        .bind(BASICS_GENRE_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        let Some(batch_end) = batch_end else {
            tx.rollback().await?;
            break;
        };

        let linked = sqlx::query(
            r#"
            INSERT INTO media_genre_link (media_id, genre_id)
            SELECT DISTINCT mei.media_id, g.id
            FROM imdb_stage_basics s
            JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
            CROSS JOIN LATERAL unnest(string_to_array(s.genres, ',')) AS gn(genre_name)
            JOIN genre g ON g.name = trim(gn.genre_name)
            WHERE s.genres IS NOT NULL AND s.genres <> '\N' AND trim(gn.genre_name) <> ''
              AND mei.media_id > $1
              AND mei.media_id <= $2
            ON CONFLICT (media_id, genre_id) DO NOTHING
            "#,
        )
        .bind(last_media_id)
        .bind(batch_end)
        .execute(&mut *tx)
        .await?
        .rows_affected();

        tx.commit().await?;
        total_genres += linked;
        last_media_id = batch_end;

        reporter
            .report(
                "genres",
                last_media_id as i64,
                Some(update_total),
                total_inserted + total_updated + total_genres,
                Some(&format!("linked {linked} genre rows")),
            )
            .await;
    }

    let total = total_inserted + total_updated + total_genres;
    info!(
        inserted = total_inserted,
        updated = total_updated,
        genres = total_genres,
        "basics merge complete"
    );
    Ok(total)
}

pub async fn merge_ratings(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    providers: &ProviderIds,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "ratings",
        rows_loaded,
    };
    let mut last_tconst = String::new();
    let mut total = 0u64;

    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;
        let affected = sqlx::query(
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
              AND s.tconst > $2
            ORDER BY s.tconst
            LIMIT $3
            ON CONFLICT (media_id, rating_provider_id, rating_type) DO UPDATE SET
                rating = EXCLUDED.rating,
                vote_count = EXCLUDED.vote_count,
                updated_at = now()
            WHERE media_rating.rating IS DISTINCT FROM EXCLUDED.rating
               OR media_rating.vote_count IS DISTINCT FROM EXCLUDED.vote_count
            "#,
        )
        .bind(providers.rating_provider_id)
        .bind(&last_tconst)
        .bind(RATINGS_BATCH)
        .execute(&mut *tx)
        .await?
        .rows_affected();

        let batch_max: Option<String> = sqlx::query_scalar::<_, Option<String>>(
            r#"
            SELECT MAX(s.tconst) FROM (
                SELECT s.tconst
                FROM imdb_stage_ratings s
                WHERE s.tconst > $1
                ORDER BY s.tconst
                LIMIT $2
            ) s
            "#,
        )
        .bind(&last_tconst)
        .bind(RATINGS_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;

        let Some(max_t) = batch_max else {
            break;
        };
        last_tconst = max_t;
        if affected == 0 {
            continue;
        }
        total += affected;
        reporter
            .report("upsert", total as i64, Some(rows_loaded), total, None)
            .await;
    }

    Ok(total)
}

pub async fn merge_names(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    providers: &ProviderIds,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "names",
        rows_loaded,
    };
    let mut last_nconst = String::new();
    let mut total = 0u64;

    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;
        let affected = sqlx::query(
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
              AND s.nconst > $2
            ORDER BY s.nconst
            LIMIT $3
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
        .bind(&last_nconst)
        .bind(NAMES_BATCH)
        .execute(&mut *tx)
        .await?
        .rows_affected();

        let batch_max: Option<String> = sqlx::query_scalar::<_, Option<String>>(
            r#"
            SELECT MAX(s.nconst) FROM (
                SELECT s.nconst
                FROM imdb_stage_names s
                WHERE s.nconst > $1
                ORDER BY s.nconst
                LIMIT $2
            ) s
            "#,
        )
        .bind(&last_nconst)
        .bind(NAMES_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;

        let Some(max_n) = batch_max else {
            break;
        };
        last_nconst = max_n;
        if affected == 0 {
            continue;
        }
        total += affected;
        reporter
            .report("upsert", total as i64, Some(rows_loaded), total, None)
            .await;
    }

    Ok(total)
}

pub async fn merge_akas(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "akas",
        rows_loaded,
    };
    let mut last_title_id = String::new();
    let mut total = 0u64;

    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;
        let affected = sqlx::query(
            r#"
            INSERT INTO aka_title (media_id, title, language_code)
            SELECT DISTINCT mei.media_id, s.title, NULLIF(s.language, '\N')
            FROM imdb_stage_akas s
            JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.title_id
            WHERE s.title IS NOT NULL AND s.title <> '' AND s.title <> '\N'
              AND s.title_id > $1
              AND NOT EXISTS (
                  SELECT 1 FROM media m
                  WHERE m.id = mei.media_id AND lower(m.title) = lower(s.title)
              )
            ORDER BY s.title_id
            LIMIT $2
            ON CONFLICT (media_id, title) DO NOTHING
            "#,
        )
        .bind(&last_title_id)
        .bind(AKAS_BATCH)
        .execute(&mut *tx)
        .await?
        .rows_affected();

        let batch_max: Option<String> = sqlx::query_scalar::<_, Option<String>>(
            r#"
            SELECT MAX(s.title_id) FROM (
                SELECT s.title_id
                FROM imdb_stage_akas s
                WHERE s.title_id > $1
                ORDER BY s.title_id
                LIMIT $2
            ) s
            "#,
        )
        .bind(&last_title_id)
        .bind(AKAS_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;

        let Some(max_id) = batch_max else {
            break;
        };
        last_title_id = max_id;
        if affected == 0 {
            continue;
        }
        total += affected;
        reporter
            .report("insert", total as i64, Some(rows_loaded), total, None)
            .await;
    }

    Ok(total)
}

pub async fn merge_episodes(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    providers: &ProviderIds,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "episode",
        rows_loaded,
    };

    {
        let mut tx = begin_long_running_tx(pool).await?;
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
        tx.commit().await?;
    }

    let mut last_tconst = String::new();
    let mut total = 0u64;

    loop {
        check_cancel(cancel)?;
        let mut tx = begin_long_running_tx(pool).await?;
        let affected = sqlx::query(
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
              AND e.tconst > $2
            ORDER BY e.tconst
            LIMIT $3
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
        .bind(&last_tconst)
        .bind(EPISODE_BATCH)
        .execute(&mut *tx)
        .await?
        .rows_affected();

        let batch_max: Option<String> = sqlx::query_scalar::<_, Option<String>>(
            r#"
            SELECT MAX(e.tconst) FROM (
                SELECT e.tconst
                FROM imdb_stage_episode e
                WHERE e.tconst > $1
                ORDER BY e.tconst
                LIMIT $2
            ) e
            "#,
        )
        .bind(&last_tconst)
        .bind(EPISODE_BATCH)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;

        let Some(max_t) = batch_max else {
            break;
        };
        last_tconst = max_t;
        if affected == 0 {
            continue;
        }
        total += affected;
        reporter
            .report("upsert", total as i64, Some(rows_loaded), total, None)
            .await;
    }

    Ok(total)
}

pub async fn merge_crew(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "crew",
        rows_loaded,
    };
    let mut total = 0u64;

    for (col, job, dept) in [
        ("directors", "Director", "Directing"),
        ("writers", "Writer", "Writing"),
    ] {
        let mut last_tconst = String::new();
        loop {
            check_cancel(cancel)?;
            let mut tx = begin_long_running_tx(pool).await?;
            let sql = format!(
                r#"
                INSERT INTO media_crew (media_id, person_id, department, job)
                SELECT DISTINCT mei.media_id, p.id, '{dept}', '{job}'
                FROM imdb_stage_crew c
                JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = c.tconst
                CROSS JOIN LATERAL unnest(string_to_array(NULLIF(c.{col}, '\N'), ',')) AS n(nconst)
                JOIN person p ON p.imdb_id = trim(n.nconst)
                WHERE trim(n.nconst) <> ''
                  AND c.tconst > $1
                  AND NOT EXISTS (
                      SELECT 1 FROM media_crew mc
                      WHERE mc.media_id = mei.media_id
                        AND mc.person_id = p.id
                        AND COALESCE(mc.job, '') = '{job}'
                  )
                ORDER BY c.tconst
                LIMIT $2
                "#
            );
            let affected = sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                .bind(&last_tconst)
                .bind(CREW_BATCH)
                .execute(&mut *tx)
                .await?
                .rows_affected();

            let batch_max: Option<String> = sqlx::query_scalar::<_, Option<String>>(
                r#"
                SELECT MAX(c.tconst) FROM (
                    SELECT c.tconst
                    FROM imdb_stage_crew c
                    WHERE c.tconst > $1
                    ORDER BY c.tconst
                    LIMIT $2
                ) c
                "#,
            )
            .bind(&last_tconst)
            .bind(CREW_BATCH)
            .fetch_one(&mut *tx)
            .await?;

            tx.commit().await?;

            let Some(max_t) = batch_max else {
                break;
            };
            last_tconst = max_t;
            if affected == 0 {
                continue;
            }
            total += affected;
            reporter
                .report(
                    &format!("{job}_batch"),
                    total as i64,
                    Some(rows_loaded),
                    total,
                    None,
                )
                .await;
        }
    }

    Ok(total)
}

pub async fn merge_principals(
    pool: &PgPool,
    redis: &RedisClient,
    started_at: &str,
    rows_loaded: i64,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    let reporter = MergeReporter {
        redis,
        started_at,
        dataset: "principals",
        rows_loaded,
    };
    let mut total = 0u64;

    for (step, cast_mode) in [("cast", true), ("crew", false)] {
        let mut last_tconst = String::new();
        loop {
            check_cancel(cancel)?;
            let mut tx = begin_long_running_tx(pool).await?;
            let affected = if cast_mode {
                sqlx::query(
                    r#"
                    INSERT INTO media_cast (media_id, person_id, "character", display_order)
                    SELECT mei.media_id, p.id,
                           NULLIF(s.characters, '\N'),
                           COALESCE(NULLIF(s.ordering, '\N')::integer, 0)
                    FROM (
                        SELECT s.*
                        FROM imdb_stage_principals s
                        WHERE lower(s.category) IN ('actor', 'actress', 'self')
                          AND s.nconst IS NOT NULL AND s.nconst <> '\N'
                          AND s.tconst > $1
                        ORDER BY s.tconst
                        LIMIT $2
                    ) s
                    JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
                    JOIN person p ON p.imdb_id = s.nconst
                    WHERE NOT EXISTS (
                        SELECT 1 FROM media_cast mc
                        WHERE mc.media_id = mei.media_id
                          AND mc.person_id = p.id
                          AND COALESCE(mc."character", '') = COALESCE(NULLIF(s.characters, '\N'), '')
                    )
                    "#,
                )
                .bind(&last_tconst)
                .bind(PRINCIPALS_BATCH)
                .execute(&mut *tx)
                .await?
                .rows_affected()
            } else {
                sqlx::query(
                    r#"
                    INSERT INTO media_crew (media_id, person_id, department, job)
                    SELECT mei.media_id, p.id,
                           initcap(s.category),
                           COALESCE(NULLIF(s.job, '\N'), initcap(s.category))
                    FROM (
                        SELECT s.*
                        FROM imdb_stage_principals s
                        WHERE lower(s.category) NOT IN ('actor', 'actress', 'self')
                          AND s.nconst IS NOT NULL AND s.nconst <> '\N'
                          AND s.tconst > $1
                        ORDER BY s.tconst
                        LIMIT $2
                    ) s
                    JOIN media_external_id mei ON mei.provider = 'imdb' AND mei.external_id = s.tconst
                    JOIN person p ON p.imdb_id = s.nconst
                    WHERE NOT EXISTS (
                        SELECT 1 FROM media_crew mc
                        WHERE mc.media_id = mei.media_id
                          AND mc.person_id = p.id
                          AND COALESCE(mc.job, '') = COALESCE(NULLIF(s.job, '\N'), initcap(s.category))
                    )
                    "#,
                )
                .bind(&last_tconst)
                .bind(PRINCIPALS_BATCH)
                .execute(&mut *tx)
                .await?
                .rows_affected()
            };

            let batch_max: Option<String> = sqlx::query_scalar::<_, Option<String>>(
                r#"
                SELECT MAX(s.tconst) FROM (
                    SELECT s.tconst
                    FROM imdb_stage_principals s
                    WHERE s.tconst > $1
                    ORDER BY s.tconst
                    LIMIT $2
                ) s
                "#,
            )
            .bind(&last_tconst)
            .bind(PRINCIPALS_BATCH)
            .fetch_one(&mut *tx)
            .await?;

            tx.commit().await?;

            let Some(max_t) = batch_max else {
                break;
            };
            last_tconst = max_t;
            if affected == 0 {
                continue;
            }
            total += affected;
            reporter
                .report(step, total as i64, Some(rows_loaded), total, None)
                .await;
        }
    }

    Ok(total)
}

pub async fn merge_dataset(
    pool: &PgPool,
    redis: &RedisClient,
    key: &str,
    rows_loaded: i64,
    started_at: &str,
    providers: &ProviderIds,
    include_adult: bool,
    cancel: &CancellationToken,
) -> Result<u64, JobError> {
    match key {
        "basics" => {
            merge_basics(
                pool,
                redis,
                started_at,
                rows_loaded,
                providers,
                include_adult,
                cancel,
            )
            .await
        }
        "ratings" => merge_ratings(pool, redis, started_at, rows_loaded, providers, cancel).await,
        "names" => merge_names(pool, redis, started_at, rows_loaded, providers, cancel).await,
        "akas" => merge_akas(pool, redis, started_at, rows_loaded, cancel).await,
        "episode" => merge_episodes(pool, redis, started_at, rows_loaded, providers, cancel).await,
        "crew" => merge_crew(pool, redis, started_at, rows_loaded, cancel).await,
        "principals" => merge_principals(pool, redis, started_at, rows_loaded, cancel).await,
        other => Err(JobError::other(format!("unknown dataset merge: {other}"))),
    }
}

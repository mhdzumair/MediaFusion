use std::sync::LazyLock;

use moka::sync::Cache;
use sqlx::PgPool;

use super::types::{LanguageLinkType, MediaId, StreamId};

// ─── Dimension-ID cache ───────────────────────────────────────────────────────
//
// audio_format / hdr_format / audio_channel / language are near-static vocabulary
// tables: new names are added rarely, ids never change, and the full cardinality is
// dozens to low-thousands of rows. Caching name→id in-process eliminates the
// hot-row write contention that was the primary cause of the production
// pool-exhaustion incident (concurrent ON CONFLICT DO UPDATE on "AAC"/"English"
// serialised on a single row lock, pinning connections for each blocked waiter).
//
// Design choices:
// - `moka::sync::Cache`: already a dependency; matches the pattern used in AppState
//   for id_cache. Using `sync` (not `future`) because cache reads are instantaneous
//   and never need to await.
// - Module-level `LazyLock` (std, stable since Rust 1.80): avoids threading
//   &AppState through the ~6 call sites that pass only &PgPool, keeping signatures
//   unchanged.
// - No TTL / no explicit eviction: dimension ids are insert-only and immutable.
//   A process restart clears the cache; that is the only necessary invalidation.
// - Capacity 10 000 per cache: comfortably over-sized; real cardinality is
//   O(10²) for audio/hdr/channel and O(10³) for languages.

struct DimCaches {
    audio_format: Cache<String, i32>,
    hdr_format: Cache<String, i32>,
    audio_channel: Cache<String, i32>,
    language: Cache<String, i32>,
}

static DIM_CACHE: LazyLock<DimCaches> = LazyLock::new(|| DimCaches {
    audio_format: Cache::new(10_000),
    hdr_format: Cache::new(10_000),
    audio_channel: Cache::new(10_000),
    language: Cache::new(10_000),
});

// ─── Private query constants ──────────────────────────────────────────────────
// Hardcoded per-table (not format strings) so query text is static and auditable.

const INSERT_AUDIO_FORMAT: &str =
    "INSERT INTO audio_format(name) SELECT UNNEST($1::text[]) ON CONFLICT DO NOTHING";
const SELECT_AUDIO_FORMAT: &str = "SELECT id, name FROM audio_format WHERE name = ANY($1::text[])";
const LINK_AUDIO_FORMAT: &str = "INSERT INTO stream_audio_link(stream_id, audio_format_id) \
     SELECT $1, UNNEST($2::int4[]) ON CONFLICT DO NOTHING";

const INSERT_HDR_FORMAT: &str =
    "INSERT INTO hdr_format(name) SELECT UNNEST($1::text[]) ON CONFLICT DO NOTHING";
const SELECT_HDR_FORMAT: &str = "SELECT id, name FROM hdr_format WHERE name = ANY($1::text[])";
const LINK_HDR_FORMAT: &str = "INSERT INTO stream_hdr_link(stream_id, hdr_format_id) \
     SELECT $1, UNNEST($2::int4[]) ON CONFLICT DO NOTHING";

const INSERT_AUDIO_CHANNEL: &str =
    "INSERT INTO audio_channel(name) SELECT UNNEST($1::text[]) ON CONFLICT DO NOTHING";
const SELECT_AUDIO_CHANNEL: &str =
    "SELECT id, name FROM audio_channel WHERE name = ANY($1::text[])";
const LINK_AUDIO_CHANNEL: &str = "INSERT INTO stream_channel_link(stream_id, channel_id) \
     SELECT $1, UNNEST($2::int4[]) ON CONFLICT DO NOTHING";

const INSERT_LANGUAGE: &str =
    "INSERT INTO language(name) SELECT UNNEST($1::text[]) ON CONFLICT DO NOTHING";
const SELECT_LANGUAGE: &str = "SELECT id, name FROM language WHERE name = ANY($1::text[])";
const LINK_LANGUAGE: &str = "INSERT INTO stream_language_link(stream_id, language_id, language_type) \
     SELECT $1, UNNEST($2::int4[]), $3 ON CONFLICT DO NOTHING";

// ─── Dimension ID resolution ──────────────────────────────────────────────────

/// Resolve a slice of dimension names to their database ids, using the in-process cache
/// for known values and a single batch INSERT + SELECT round-trip for any misses.
///
/// # Cache-miss flow (first call per name, or cold start)
/// 1. `INSERT … SELECT UNNEST($1::text[]) ON CONFLICT DO NOTHING` — inserts genuinely
///    new names; if a name already exists `DO NOTHING` skips it **without taking a
///    row lock** (unlike the former `DO UPDATE SET name = EXCLUDED.name` which locked
///    the hot row on every ingest).
/// 2. `SELECT id, name … WHERE name = ANY($1)` — fetches ids for all missed names,
///    whether they were just inserted or already existed (handles concurrent insert races
///    correctly: both racers get the same stable id, and neither drops the link).
/// 3. All resolved ids are inserted into the cache.
///
/// # Warm path (steady state)
/// All names are cache hits → zero DB I/O from this function; only the caller's link
/// insert runs.
async fn batch_dim_ids(
    pool: &PgPool,
    cache: &Cache<String, i32>,
    insert_sql: &'static str,
    select_sql: &'static str,
    names: &[String],
) -> Result<Vec<i32>, sqlx::Error> {
    if names.is_empty() {
        return Ok(vec![]);
    }

    let mut ids: Vec<i32> = Vec::with_capacity(names.len());
    // Collect unique cache misses (preserve order for determinism; O(n) for small n).
    let mut misses: Vec<String> = Vec::new();

    for name in names {
        match cache.get(name) {
            Some(id) => ids.push(id),
            None => {
                if !misses.iter().any(|m| m == name) {
                    misses.push(name.clone());
                }
            }
        }
    }

    if !misses.is_empty() {
        let miss_strs: Vec<&str> = misses.iter().map(String::as_str).collect();

        // Insert new names; DO NOTHING avoids the hot-row lock on existing vocab.
        sqlx::query(insert_sql)
            .bind(&miss_strs[..])
            .execute(pool)
            .await?;

        // Fetch ids for ALL missed names (covers both new inserts and pre-existing rows,
        // handling concurrent insert races without dropping any link).
        let resolved: Vec<(i32, String)> = sqlx::query_as(select_sql)
            .bind(&miss_strs[..])
            .fetch_all(pool)
            .await?;

        for (id, name) in resolved {
            cache.insert(name, id);
            ids.push(id);
        }
    }

    Ok(ids)
}

// ─── Public link helpers ──────────────────────────────────────────────────────

/// Link stream ↔ media and bump `total_streams` when the link is new.
pub async fn link_stream_to_media(
    pool: &PgPool,
    stream_id: StreamId,
    media_id: MediaId,
) -> Result<(), sqlx::Error> {
    let inserted: Option<(i32,)> = sqlx::query_as(
        r#"INSERT INTO stream_media_link(stream_id, media_id, is_primary, is_verified, created_at)
           SELECT $1, $2, true, false, NOW()
           WHERE NOT EXISTS (
               SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2
           )
           RETURNING 1"#,
    )
    .bind(stream_id)
    .bind(media_id)
    .fetch_optional(pool)
    .await?;

    if inserted.is_some() {
        sqlx::query(
            r#"UPDATE media SET
                   total_streams = total_streams + 1,
                   last_stream_added = GREATEST(COALESCE(last_stream_added, NOW()), NOW())
               WHERE id = $1"#,
        )
        .bind(media_id)
        .execute(pool)
        .await?;
    }

    Ok(())
}

/// Link stream ↔ media with explicit primary/verified flags.
pub async fn link_stream_to_media_with_flags(
    pool: &PgPool,
    stream_id: StreamId,
    media_id: MediaId,
    is_primary: bool,
    is_verified: bool,
) -> Result<(), sqlx::Error> {
    let inserted: Option<(i32,)> = sqlx::query_as(
        r#"INSERT INTO stream_media_link(stream_id, media_id, is_primary, is_verified, created_at)
           SELECT $1, $2, $3, $4, NOW()
           WHERE NOT EXISTS (
               SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2
           )
           RETURNING 1"#,
    )
    .bind(stream_id)
    .bind(media_id)
    .bind(is_primary)
    .bind(is_verified)
    .fetch_optional(pool)
    .await?;

    if inserted.is_some() {
        sqlx::query(
            r#"UPDATE media SET
                   total_streams = total_streams + 1,
                   last_stream_added = GREATEST(COALESCE(last_stream_added, NOW()), NOW())
               WHERE id = $1"#,
        )
        .bind(media_id)
        .execute(pool)
        .await?;
    }

    Ok(())
}

/// Link audio format names to a stream.
///
/// Resolves name→id via the in-process cache (batch DB fetch on miss), then inserts
/// all links in a single statement. In warm steady state: one DB round-trip (link
/// insert only). Cold: two round-trips (INSERT+SELECT for misses, then link insert).
pub async fn link_stream_audio_formats(
    pool: &PgPool,
    stream_id: i32,
    formats: &[String],
) -> Result<(), sqlx::Error> {
    let formats: Vec<&String> = formats.iter().filter(|n| !n.is_empty()).collect();
    if formats.is_empty() {
        return Ok(());
    }
    let owned: Vec<String> = formats.iter().map(|s| (*s).clone()).collect();
    let ids = batch_dim_ids(
        pool,
        &DIM_CACHE.audio_format,
        INSERT_AUDIO_FORMAT,
        SELECT_AUDIO_FORMAT,
        &owned,
    )
    .await?;
    if !ids.is_empty() {
        sqlx::query(LINK_AUDIO_FORMAT)
            .bind(stream_id)
            .bind(&ids[..])
            .execute(pool)
            .await?;
    }
    Ok(())
}

/// Link HDR format names to a stream.
pub async fn link_stream_hdr_formats(
    pool: &PgPool,
    stream_id: i32,
    formats: &[String],
) -> Result<(), sqlx::Error> {
    let formats: Vec<&String> = formats.iter().filter(|n| !n.is_empty()).collect();
    if formats.is_empty() {
        return Ok(());
    }
    let owned: Vec<String> = formats.iter().map(|s| (*s).clone()).collect();
    let ids = batch_dim_ids(
        pool,
        &DIM_CACHE.hdr_format,
        INSERT_HDR_FORMAT,
        SELECT_HDR_FORMAT,
        &owned,
    )
    .await?;
    if !ids.is_empty() {
        sqlx::query(LINK_HDR_FORMAT)
            .bind(stream_id)
            .bind(&ids[..])
            .execute(pool)
            .await?;
    }
    Ok(())
}

/// Link audio channel names to a stream.
pub async fn link_stream_audio_channels(
    pool: &PgPool,
    stream_id: i32,
    channels: &[String],
) -> Result<(), sqlx::Error> {
    let channels: Vec<&String> = channels.iter().filter(|n| !n.is_empty()).collect();
    if channels.is_empty() {
        return Ok(());
    }
    let owned: Vec<String> = channels.iter().map(|s| (*s).clone()).collect();
    let ids = batch_dim_ids(
        pool,
        &DIM_CACHE.audio_channel,
        INSERT_AUDIO_CHANNEL,
        SELECT_AUDIO_CHANNEL,
        &owned,
    )
    .await?;
    if !ids.is_empty() {
        sqlx::query(LINK_AUDIO_CHANNEL)
            .bind(stream_id)
            .bind(&ids[..])
            .execute(pool)
            .await?;
    }
    Ok(())
}

/// Link audio track languages to a stream.
pub async fn link_stream_languages(
    pool: &PgPool,
    stream_id: i32,
    languages: &[String],
) -> Result<(), sqlx::Error> {
    link_stream_languages_typed(pool, stream_id, languages, LanguageLinkType::Audio).await
}

/// Link languages with an explicit [`LanguageLinkType`] (audio or subtitle).
pub async fn link_stream_languages_typed(
    pool: &PgPool,
    stream_id: i32,
    languages: &[String],
    language_type: LanguageLinkType,
) -> Result<(), sqlx::Error> {
    let languages: Vec<&String> = languages.iter().filter(|n| !n.is_empty()).collect();
    if languages.is_empty() {
        return Ok(());
    }
    let owned: Vec<String> = languages.iter().map(|s| (*s).clone()).collect();
    let ids = batch_dim_ids(
        pool,
        &DIM_CACHE.language,
        INSERT_LANGUAGE,
        SELECT_LANGUAGE,
        &owned,
    )
    .await?;
    if !ids.is_empty() {
        sqlx::query(LINK_LANGUAGE)
            .bind(stream_id)
            .bind(&ids[..])
            .bind(language_type.as_str())
            .execute(pool)
            .await?;
    }
    Ok(())
}

/// Link announce tracker URLs to a torrent via `stream.id`.
pub async fn link_torrent_trackers_for_stream(
    pool: &PgPool,
    stream_id: StreamId,
    tracker_urls: &[String],
) -> Result<(), sqlx::Error> {
    super::streams::link_torrent_trackers(pool, stream_id, tracker_urls).await
}

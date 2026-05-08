/// Cold-path result persistence.
///
/// For each scraped stream:
///   1. Skip if info_hash already in torrent_stream (update seeders only).
///   2. Otherwise: insert stream → torrent_stream → stream_file → file_media_link
///      (series) or stream_media_link (movie).
///
/// After all inserts, encode the full stream list as a Python-compatible
/// \x01MFsc1 + zlib blob and write it to the Redis stream_data key so future
/// warm-path requests skip the cold path entirely.
use serde_json::json;
use sqlx::PgPool;
use tracing::{debug, warn};

use crate::{
    cache::{codec, stream_cache},
    scrapers::{ScrapedStream, ScrapedTelegramStream, ScrapedUsenetStream, SearchMeta},
};

#[allow(clippy::too_many_arguments)]
pub async fn write_back(
    streams: &[ScrapedStream],
    pool: &PgPool,
    redis: &fred::clients::Client,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) {
    if streams.is_empty() {
        return;
    }

    let mut persisted_hashes: Vec<String> = Vec::new();

    for s in streams {
        match upsert_stream(pool, s, meta, media_type, season, episode).await {
            Ok(inserted) => {
                if inserted {
                    persisted_hashes.push(s.info_hash.clone());
                }
            }
            Err(e) => {
                warn!("persist: failed to upsert {} — {e}", s.info_hash);
            }
        }
    }

    debug!(
        "persist: {} new, {} skipped for {}",
        persisted_hashes.len(),
        streams.len() - persisted_hashes.len(),
        meta.media_id
    );

    // Write Redis blob for the primary media_id so future warm-path requests hit cache.
    let key = redis_key(meta.media_id, media_type, season, episode, scope);
    let blob_json = json!({
        "torrents": streams.iter().map(|s| {
            json!({
                "name": s.name,
                "info_hash": s.info_hash,
                "quality": s.parsed.quality,
                "resolution": s.parsed.resolution,
                "is_public": true
            })
        }).collect::<Vec<_>>()
    });
    if let Some(blob) = codec::encode_blob(&blob_json) {
        if let Err(e) = stream_cache::set_with_ttl(redis, &key, blob, 900).await {
            warn!("persist: redis write failed for {key}: {e}");
        }
    }
}

// ─── DB upsert ────────────────────────────────────────────────────────────────

/// Returns `true` if a new row was inserted, `false` if the info_hash already existed.
async fn upsert_stream(
    pool: &PgPool,
    s: &ScrapedStream,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<bool, sqlx::Error> {
    // Check existing
    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
            .bind(&s.info_hash)
            .fetch_optional(pool)
            .await?;

    if let Some((stream_id,)) = existing {
        // Just refresh seeders
        if let Some(seeders) = s.seeders {
            sqlx::query(
                "UPDATE torrent_stream SET seeders = GREATEST(seeders, $1) WHERE stream_id = $2",
            )
            .bind(seeders)
            .bind(stream_id)
            .execute(pool)
            .await
            .ok();
        }
        return Ok(false);
    }

    // Insert base stream row
    let (stream_id,): (i32,) = sqlx::query_as(
        r#"
        INSERT INTO stream (
            stream_type, name, source,
            resolution, codec, quality,
            is_proper, is_repack, is_extended, is_complete, is_dubbed,
            release_group,
            is_active, is_blocked, is_public, playback_count,
            created_at
        ) VALUES (
            'TORRENT'::streamtype, $1, $2,
            $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11,
            true, false, true, 0,
            NOW()
        )
        RETURNING id
        "#,
    )
    .bind(&s.name)
    .bind(&s.source)
    .bind(&s.parsed.resolution)
    .bind(&s.parsed.codec)
    .bind(&s.parsed.quality)
    .bind(s.parsed.is_proper)
    .bind(s.parsed.is_repack)
    .bind(s.parsed.is_extended)
    .bind(s.parsed.is_complete)
    .bind(s.parsed.is_dubbed)
    .bind(&s.parsed.release_group)
    .fetch_one(pool)
    .await?;

    // Insert torrent_stream row
    let ts_result = sqlx::query(
        r#"
        INSERT INTO torrent_stream (
            stream_id, info_hash, total_size, seeders, torrent_type, file_count, created_at
        ) VALUES (
            $1, $2, $3, $4, 'PUBLIC'::torrenttype, 1, NOW()
        )
        ON CONFLICT (info_hash) DO NOTHING
        "#,
    )
    .bind(stream_id)
    .bind(&s.info_hash)
    .bind(s.size.unwrap_or(0))
    .bind(s.seeders)
    .execute(pool)
    .await;

    // If the ON CONFLICT fired (rows_affected == 0), someone else inserted the same hash
    // between our SELECT and INSERT.  The stream row we just inserted is now orphaned.
    // Clean it up and bail.
    if let Ok(r) = &ts_result {
        if r.rows_affected() == 0 {
            sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id)
                .execute(pool)
                .await
                .ok();
            return Ok(false);
        }
    }
    ts_result?;

    // Link stream to media
    if media_type == "series" && !s.files.is_empty() {
        for f in &s.files {
            // Insert stream_file row
            let file_result: Result<(i32,), _> = sqlx::query_as(
                r#"
                INSERT INTO stream_file (stream_id, file_index, filename, file_type)
                VALUES ($1, $2, $3, 'VIDEO'::filetype)
                ON CONFLICT (stream_id, file_index) DO NOTHING
                RETURNING id
                "#,
            )
            .bind(stream_id)
            .bind(f.file_index)
            .bind(&f.filename)
            .fetch_one(pool)
            .await;

            if let Ok((file_id,)) = file_result {
                sqlx::query(
                    r#"
                    INSERT INTO file_media_link
                        (file_id, media_id, season_number, episode_number,
                         is_primary, confidence, link_source)
                    VALUES ($1, $2, $3, $4, true, 1.0, 'PTT_PARSER'::linksource)
                    ON CONFLICT (file_id, media_id, season_number, episode_number) DO NOTHING
                    "#,
                )
                .bind(file_id)
                .bind(meta.media_id as i32)
                .bind(f.season_number)
                .bind(f.episode_number)
                .execute(pool)
                .await
                .ok();
            }
        }
    } else {
        // Movie (or series with no file breakdown): use stream_media_link
        // Guard against races: only insert if the link doesn't exist yet.
        sqlx::query(
            r#"
            INSERT INTO stream_media_link (stream_id, media_id, is_primary)
            SELECT $1, $2, true
            WHERE NOT EXISTS (
                SELECT 1 FROM stream_media_link
                WHERE stream_id = $1 AND media_id = $2
            )
            "#,
        )
        .bind(stream_id)
        .bind(meta.media_id as i32)
        .execute(pool)
        .await
        .ok();

        // If the request specified season/episode but no file breakdown, also add
        // a file_media_link via a synthetic stream_file row.
        if let (Some(s_num), Some(e_num)) = (season, episode) {
            let sf_result: Result<(i32,), _> = sqlx::query_as(
                r#"
                INSERT INTO stream_file (stream_id, file_index, filename, file_type)
                VALUES ($1, 0, '', 'VIDEO'::filetype)
                ON CONFLICT (stream_id, file_index) DO NOTHING
                RETURNING id
                "#,
            )
            .bind(stream_id)
            .fetch_one(pool)
            .await;

            if let Ok((file_id,)) = sf_result {
                sqlx::query(
                    r#"
                    INSERT INTO file_media_link
                        (file_id, media_id, season_number, episode_number,
                         is_primary, confidence, link_source)
                    VALUES ($1, $2, $3, $4, true, 1.0, 'PTT_PARSER'::linksource)
                    ON CONFLICT (file_id, media_id, season_number, episode_number) DO NOTHING
                    "#,
                )
                .bind(file_id)
                .bind(meta.media_id as i32)
                .bind(s_num)
                .bind(e_num)
                .execute(pool)
                .await
                .ok();
            }
        }
    }

    Ok(true)
}

// ─── Usenet persistence ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub async fn write_back_usenet(
    streams: &[ScrapedUsenetStream],
    pool: &PgPool,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) {
    if streams.is_empty() {
        return;
    }

    let mut inserted = 0usize;
    for s in streams {
        match upsert_usenet_stream(pool, s, meta, media_type, season, episode).await {
            Ok(true) => inserted += 1,
            Ok(false) => {}
            Err(e) => tracing::warn!("persist usenet: failed {} — {e}", s.nzb_guid),
        }
    }

    debug!(
        "persist usenet: {} new, {} skipped for {}",
        inserted,
        streams.len() - inserted,
        meta.media_id
    );
}

async fn upsert_usenet_stream(
    pool: &PgPool,
    s: &ScrapedUsenetStream,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<bool, sqlx::Error> {
    // Skip if already exists
    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM usenet_stream WHERE nzb_guid = $1")
            .bind(&s.nzb_guid)
            .fetch_optional(pool)
            .await?;
    if existing.is_some() {
        return Ok(false);
    }

    // Insert base stream row
    let (stream_id,): (i32,) = sqlx::query_as(
        r#"
        INSERT INTO stream (
            stream_type, name, source,
            resolution, codec, quality,
            is_proper, is_repack, is_extended, is_complete, is_dubbed,
            release_group,
            is_active, is_blocked, is_public, playback_count,
            created_at
        ) VALUES (
            'USENET'::streamtype, $1, $2,
            $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11,
            true, false, true, 0,
            NOW()
        )
        RETURNING id
        "#,
    )
    .bind(&s.name)
    .bind(&s.source)
    .bind(&s.parsed.resolution)
    .bind(&s.parsed.codec)
    .bind(&s.parsed.quality)
    .bind(s.parsed.is_proper)
    .bind(s.parsed.is_repack)
    .bind(s.parsed.is_extended)
    .bind(s.parsed.is_complete)
    .bind(s.parsed.is_dubbed)
    .bind(&s.parsed.release_group)
    .fetch_one(pool)
    .await?;

    // Insert usenet_stream row
    let us_result = sqlx::query(
        r#"
        INSERT INTO usenet_stream (
            stream_id, nzb_guid, nzb_url, size, indexer, group_name,
            is_passworded
        ) VALUES (
            $1, $2, $3, $4, $5, $6, false
        )
        ON CONFLICT (nzb_guid) DO NOTHING
        "#,
    )
    .bind(stream_id)
    .bind(&s.nzb_guid)
    .bind(&s.nzb_url)
    .bind(s.size)
    .bind(&s.indexer)
    .bind(&s.group_name)
    .execute(pool)
    .await;

    if let Ok(r) = &us_result {
        if r.rows_affected() == 0 {
            sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id)
                .execute(pool)
                .await
                .ok();
            return Ok(false);
        }
    }
    us_result?;

    // Link to media
    if media_type == "series" && !s.files.is_empty() {
        for f in &s.files {
            let sf: Result<(i32,), _> = sqlx::query_as(
                r#"
                INSERT INTO stream_file (stream_id, file_index, filename, file_type)
                VALUES ($1, $2, $3, 'VIDEO'::filetype)
                ON CONFLICT (stream_id, file_index) DO NOTHING
                RETURNING id
                "#,
            )
            .bind(stream_id)
            .bind(f.file_index)
            .bind(&f.filename)
            .fetch_one(pool)
            .await;

            if let Ok((file_id,)) = sf {
                sqlx::query(
                    r#"
                    INSERT INTO file_media_link
                        (file_id, media_id, season_number, episode_number,
                         is_primary, confidence, link_source)
                    VALUES ($1, $2, $3, $4, true, 1.0, 'PTT_PARSER'::linksource)
                    ON CONFLICT (file_id, media_id, season_number, episode_number) DO NOTHING
                    "#,
                )
                .bind(file_id)
                .bind(meta.media_id as i32)
                .bind(f.season_number)
                .bind(f.episode_number)
                .execute(pool)
                .await
                .ok();
            }
        }
    } else {
        sqlx::query(
            r#"
            INSERT INTO stream_media_link (stream_id, media_id, is_primary)
            SELECT $1, $2, true
            WHERE NOT EXISTS (
                SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2
            )
            "#,
        )
        .bind(stream_id)
        .bind(meta.media_id as i32)
        .execute(pool)
        .await
        .ok();

        if let (Some(s_num), Some(e_num)) = (season, episode) {
            let sf: Result<(i32,), _> = sqlx::query_as(
                r#"
                INSERT INTO stream_file (stream_id, file_index, filename, file_type)
                VALUES ($1, 0, '', 'VIDEO'::filetype)
                ON CONFLICT (stream_id, file_index) DO NOTHING
                RETURNING id
                "#,
            )
            .bind(stream_id)
            .fetch_one(pool)
            .await;

            if let Ok((file_id,)) = sf {
                sqlx::query(
                    r#"
                    INSERT INTO file_media_link
                        (file_id, media_id, season_number, episode_number,
                         is_primary, confidence, link_source)
                    VALUES ($1, $2, $3, $4, true, 1.0, 'PTT_PARSER'::linksource)
                    ON CONFLICT (file_id, media_id, season_number, episode_number) DO NOTHING
                    "#,
                )
                .bind(file_id)
                .bind(meta.media_id as i32)
                .bind(s_num)
                .bind(e_num)
                .execute(pool)
                .await
                .ok();
            }
        }
    }

    Ok(true)
}

// ─── Telegram persistence ─────────────────────────────────────────────────────

/// Persist a slice of Telegram document streams to the database.
///
/// For each stream:
/// 1. Skip if a `telegram_stream` row with the same `(chat_id, message_id)` already exists.
/// 2. Otherwise insert `stream` → `telegram_stream` → `stream_media_link`.
#[allow(clippy::too_many_arguments)]
pub async fn write_telegram_streams(
    streams: &[ScrapedTelegramStream],
    pool: &PgPool,
    meta: &SearchMeta,
    _media_type: &str,
    _season: Option<i32>,
    _episode: Option<i32>,
) {
    if streams.is_empty() {
        return;
    }

    let mut inserted = 0usize;
    for s in streams {
        match upsert_telegram_stream(pool, s, meta).await {
            Ok(true) => inserted += 1,
            Ok(false) => {}
            Err(e) => {
                warn!(
                    "persist telegram: failed chat={} msg={} — {e}",
                    s.chat_id, s.message_id
                );
            }
        }
    }

    debug!(
        "persist telegram: {} new, {} skipped for {}",
        inserted,
        streams.len() - inserted,
        meta.media_id
    );
}

async fn upsert_telegram_stream(
    pool: &PgPool,
    s: &ScrapedTelegramStream,
    meta: &SearchMeta,
) -> Result<bool, sqlx::Error> {
    // Check for duplicate (chat_id stored as TEXT, message_id as INT)
    let existing: Option<(i32,)> = sqlx::query_as(
        "SELECT stream_id FROM telegram_stream WHERE chat_id = $1 AND message_id = $2 LIMIT 1",
    )
    .bind(s.chat_id.to_string())
    .bind(s.message_id)
    .fetch_optional(pool)
    .await?;

    if existing.is_some() {
        return Ok(false);
    }

    // Insert base stream row
    let (stream_id,): (i32,) = sqlx::query_as(
        r#"
        INSERT INTO stream (
            stream_type, name, source,
            resolution, codec, quality,
            is_proper, is_repack, is_extended, is_complete, is_dubbed,
            release_group,
            is_active, is_blocked, is_public, playback_count,
            created_at
        ) VALUES (
            'TELEGRAM'::streamtype, $1, 'Telegram',
            $2, $3, $4,
            $5, $6, $7, $8, $9,
            $10,
            true, false, true, 0,
            NOW()
        )
        RETURNING id
        "#,
    )
    .bind(&s.name)
    .bind(&s.parsed.resolution)
    .bind(&s.parsed.codec)
    .bind(&s.parsed.quality)
    .bind(s.parsed.is_proper)
    .bind(s.parsed.is_repack)
    .bind(s.parsed.is_extended)
    .bind(s.parsed.is_complete)
    .bind(s.parsed.is_dubbed)
    .bind(&s.parsed.release_group)
    .fetch_one(pool)
    .await?;

    // Insert telegram_stream row
    let ts_result = sqlx::query(
        r#"
        INSERT INTO telegram_stream (
            stream_id, chat_id, chat_username, message_id, file_name, size, mime_type
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7
        )
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(stream_id)
    .bind(s.chat_id.to_string())
    .bind(&s.chat_username)
    .bind(s.message_id)
    .bind(&s.file_name)
    .bind(s.size)
    .bind(&s.mime_type)
    .execute(pool)
    .await;

    // Race condition guard: if ON CONFLICT fired, clean up orphan stream row
    if let Ok(r) = &ts_result {
        if r.rows_affected() == 0 {
            sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id)
                .execute(pool)
                .await
                .ok();
            return Ok(false);
        }
    }
    ts_result?;

    // Link stream to media item
    sqlx::query(
        r#"
        INSERT INTO stream_media_link (stream_id, media_id, is_primary)
        SELECT $1, $2, true
        WHERE NOT EXISTS (
            SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2
        )
        "#,
    )
    .bind(stream_id)
    .bind(meta.media_id as i32)
    .execute(pool)
    .await
    .ok();

    Ok(true)
}

fn redis_key(
    media_id: i64,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) -> String {
    match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => {
            format!("stream_data:series:{media_id}:{s}:{e}:{scope}")
        }
        _ => format!("stream_data:movie:{media_id}:{scope}"),
    }
}

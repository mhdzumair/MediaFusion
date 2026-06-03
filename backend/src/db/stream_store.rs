use sqlx::PgPool;
use tracing::debug;

use super::stream_links::{link_stream_to_media, link_stream_to_media_with_flags};
use super::stream_model::{
    AcestreamStoreInput, HttpStoreInput, StoreStreamOpts, StreamFileStoreInput, StreamStoreBase,
    TelegramStoreInput, TorrentStoreInput, UsenetStoreInput, YoutubeStoreInput,
};
use super::types::{FileType, LinkSource, MediaId, MediaType, StreamId, StreamType};

/// Strip NUL bytes before inserting into Postgres text columns.
#[inline]
pub fn strip_nul(s: &str) -> std::borrow::Cow<'_, str> {
    if s.contains('\0') {
        std::borrow::Cow::Owned(s.replace('\0', ""))
    } else {
        std::borrow::Cow::Borrowed(s)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreStreamResult {
    Inserted(StreamId),
    AlreadyExists(StreamId),
}

impl StoreStreamResult {
    pub fn stream_id(self) -> StreamId {
        match self {
            Self::Inserted(id) | Self::AlreadyExists(id) => id,
        }
    }

    pub fn was_inserted(self) -> bool {
        matches!(self, Self::Inserted(_))
    }
}

/// Batch store torrent streams (scraper cold-path).
pub async fn store_torrent_streams(
    pool: &PgPool,
    streams: &[TorrentStoreInput],
    opts: &StoreStreamOpts,
) {
    if streams.is_empty() || opts.media_id.0 <= 0 {
        return;
    }

    let mut inserted = 0usize;
    for stream in streams {
        match store_torrent_stream(pool, stream, opts).await {
            Ok(r) if r.was_inserted() => inserted += 1,
            Ok(_) => {}
            Err(e) => tracing::warn!("store_torrent_stream: failed {} — {e}", stream.info_hash),
        }
    }

    debug!(
        "store_torrent_streams: {} inserted, {} in batch for media {}",
        inserted,
        streams.len(),
        opts.media_id.0
    );
}

/// Idempotent torrent store keyed by `info_hash`.
pub async fn store_torrent_stream(
    pool: &PgPool,
    stream: &TorrentStoreInput,
    opts: &StoreStreamOpts,
) -> Result<StoreStreamResult, sqlx::Error> {
    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
            .bind(&stream.info_hash)
            .fetch_optional(pool)
            .await?;

    if let Some((stream_id,)) = existing {
        if let Some(seeders) = stream.seeders {
            sqlx::query(
                "UPDATE torrent_stream SET seeders = GREATEST(seeders, $1) WHERE stream_id = $2",
            )
            .bind(seeders)
            .bind(stream_id)
            .execute(pool)
            .await
            .ok();
        }
        return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
    }

    let stream_id = insert_base_stream(pool, &stream.base, StreamType::Torrent).await?;

    let file_count = if stream.files.is_empty() {
        1
    } else {
        stream.files.len() as i32
    };

    let ts_result = sqlx::query(
        r#"
        INSERT INTO torrent_stream (
            stream_id, info_hash, total_size, seeders, torrent_type, file_count, torrent_file, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, NOW()
        )
        ON CONFLICT (info_hash) DO NOTHING
        "#,
    )
    .bind(stream_id.0)
    .bind(&stream.info_hash)
    .bind(stream.total_size)
    .bind(stream.seeders)
    .bind(stream.torrent_type)
    .bind(file_count)
    .bind(stream.torrent_file.as_deref())
    .execute(pool)
    .await;

    if let Ok(r) = &ts_result {
        if r.rows_affected() == 0 {
            sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id.0)
                .execute(pool)
                .await
                .ok();
            let existing: i32 =
                sqlx::query_scalar("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
                    .bind(&stream.info_hash)
                    .fetch_one(pool)
                    .await?;
            return Ok(StoreStreamResult::AlreadyExists(StreamId(existing)));
        }
    }
    ts_result?;

    if !stream.announce_list.is_empty() {
        let _ = super::streams::link_torrent_trackers(pool, stream_id, &stream.announce_list).await;
    }

    link_torrent_to_media(pool, stream_id, &stream.files, opts).await?;
    link_stream_parsed_metadata(pool, stream_id, &stream.base).await;

    Ok(StoreStreamResult::Inserted(stream_id))
}

/// Batch store usenet streams.
pub async fn store_usenet_streams(
    pool: &PgPool,
    streams: &[UsenetStoreInput],
    opts: &StoreStreamOpts,
) {
    if streams.is_empty() {
        return;
    }
    let mut inserted = 0usize;
    for stream in streams {
        match store_usenet_stream(pool, stream, opts).await {
            Ok(r) if r.was_inserted() => inserted += 1,
            Ok(_) => {}
            Err(e) => tracing::warn!("store_usenet_stream: failed {} — {e}", stream.nzb_guid),
        }
    }
    debug!(
        "store_usenet_streams: {} new for media {}",
        inserted, opts.media_id.0
    );
}

/// Idempotent usenet store keyed by `nzb_guid`.
pub async fn store_usenet_stream(
    pool: &PgPool,
    stream: &UsenetStoreInput,
    opts: &StoreStreamOpts,
) -> Result<StoreStreamResult, sqlx::Error> {
    if opts.media_id.0 <= 0 {
        return Ok(StoreStreamResult::AlreadyExists(StreamId(0)));
    }

    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM usenet_stream WHERE nzb_guid = $1")
            .bind(&stream.nzb_guid)
            .fetch_optional(pool)
            .await?;
    if let Some((stream_id,)) = existing {
        return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
    }

    let base = sanitize_base(&stream.base);
    let stream_id = insert_base_stream(pool, &base, StreamType::Usenet).await?;

    let us_result = sqlx::query(
        r#"
        INSERT INTO usenet_stream (
            stream_id, nzb_guid, nzb_url, size, indexer, group_name, is_passworded
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7
        )
        ON CONFLICT (nzb_guid) DO NOTHING
        "#,
    )
    .bind(stream_id.0)
    .bind(&stream.nzb_guid)
    .bind(strip_nul(&stream.nzb_url))
    .bind(stream.size)
    .bind(strip_nul(&stream.indexer))
    .bind(
        stream
            .group_name
            .as_deref()
            .map(|s| strip_nul(s).into_owned()),
    )
    .bind(stream.is_passworded)
    .execute(pool)
    .await;

    if let Ok(r) = &us_result {
        if r.rows_affected() == 0 {
            cleanup_orphan_stream(pool, stream_id).await;
            let existing: i32 =
                sqlx::query_scalar("SELECT stream_id FROM usenet_stream WHERE nzb_guid = $1")
                    .bind(&stream.nzb_guid)
                    .fetch_one(pool)
                    .await?;
            return Ok(StoreStreamResult::AlreadyExists(StreamId(existing)));
        }
    }
    us_result?;

    link_files_or_media(pool, stream_id, &stream.files, opts).await?;
    link_stream_parsed_metadata(pool, stream_id, &stream.base).await;

    Ok(StoreStreamResult::Inserted(stream_id))
}

/// Batch store telegram streams.
pub async fn store_telegram_streams(
    pool: &PgPool,
    streams: &[TelegramStoreInput],
    opts: &StoreStreamOpts,
) {
    if streams.is_empty() {
        return;
    }
    let mut inserted = 0usize;
    for stream in streams {
        match store_telegram_stream(pool, stream, opts).await {
            Ok(r) if r.was_inserted() => inserted += 1,
            Ok(_) => {}
            Err(e) => tracing::warn!(
                "store_telegram_stream: failed chat={} msg={} — {e}",
                stream.chat_id,
                stream.message_id
            ),
        }
    }
    debug!(
        "store_telegram_streams: {} new for media {}",
        inserted, opts.media_id.0
    );
}

/// Idempotent telegram store keyed by `(chat_id, message_id)`.
pub async fn store_telegram_stream(
    pool: &PgPool,
    stream: &TelegramStoreInput,
    opts: &StoreStreamOpts,
) -> Result<StoreStreamResult, sqlx::Error> {
    if opts.media_id.0 <= 0 {
        return Ok(StoreStreamResult::AlreadyExists(StreamId(0)));
    }

    if let Some(ref fuid) = stream.file_unique_id {
        let existing: Option<(i32,)> = sqlx::query_as(
            "SELECT stream_id FROM telegram_stream WHERE file_unique_id = $1 LIMIT 1",
        )
        .bind(fuid)
        .fetch_optional(pool)
        .await?;
        if let Some((stream_id,)) = existing {
            return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
        }
    }

    let existing: Option<(i32,)> = sqlx::query_as(
        "SELECT stream_id FROM telegram_stream WHERE chat_id = $1 AND message_id = $2 LIMIT 1",
    )
    .bind(&stream.chat_id)
    .bind(stream.message_id)
    .fetch_optional(pool)
    .await?;

    if let Some((stream_id,)) = existing {
        return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
    }

    let mut base = stream.base.clone();
    if base.source.is_empty() {
        base.source = "Telegram".to_string();
    }
    let stream_id = insert_base_stream(pool, &base, StreamType::Telegram).await?;

    let ts_result = if stream.file_id.is_some() || stream.file_unique_id.is_some() {
        sqlx::query(
            r#"
            INSERT INTO telegram_stream (
                stream_id, chat_id, chat_username, message_id, file_name, size, mime_type,
                file_id, file_unique_id, backup_chat_id, backup_message_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
            )
            ON CONFLICT (stream_id) DO NOTHING
            "#,
        )
        .bind(stream_id.0)
        .bind(&stream.chat_id)
        .bind(&stream.chat_username)
        .bind(stream.message_id)
        .bind(&stream.file_name)
        .bind(stream.size)
        .bind(&stream.mime_type)
        .bind(&stream.file_id)
        .bind(&stream.file_unique_id)
        .bind(&stream.backup_chat_id)
        .bind(stream.backup_message_id)
        .execute(pool)
        .await
    } else {
        sqlx::query(
            r#"
            INSERT INTO telegram_stream (
                stream_id, chat_id, chat_username, message_id, file_name, size, mime_type
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7
            )
            ON CONFLICT (stream_id) DO NOTHING
            "#,
        )
        .bind(stream_id.0)
        .bind(&stream.chat_id)
        .bind(&stream.chat_username)
        .bind(stream.message_id)
        .bind(&stream.file_name)
        .bind(stream.size)
        .bind(&stream.mime_type)
        .execute(pool)
        .await
    };

    if let Ok(r) = &ts_result {
        if r.rows_affected() == 0 {
            cleanup_orphan_stream(pool, stream_id).await;
            return Ok(StoreStreamResult::AlreadyExists(stream_id));
        }
    }
    ts_result?;

    link_stream_to_media_with_flags(
        pool,
        stream_id,
        opts.media_id,
        opts.is_primary,
        opts.is_verified,
    )
    .await?;

    if opts.media_type == super::types::MediaType::Series {
        if let (Some(s), Some(e)) = (opts.season, opts.episode) {
            link_synthetic_episode_file(pool, stream_id, opts.media_id, s, e, opts).await?;
        }
    }

    Ok(StoreStreamResult::Inserted(stream_id))
}

/// Store an HTTP stream; deduplicates on `(url, media_id)` when media is set.
pub async fn store_http_stream(
    pool: &PgPool,
    stream: &HttpStoreInput,
    opts: &StoreStreamOpts,
) -> Result<StoreStreamResult, sqlx::Error> {
    if opts.media_id.0 > 0 {
        let existing: Option<i32> = sqlx::query_scalar(
            "SELECT hs.stream_id FROM http_stream hs \
             JOIN stream_media_link sml ON sml.stream_id = hs.stream_id \
             WHERE hs.url = $1 AND sml.media_id = $2 LIMIT 1",
        )
        .bind(&stream.url)
        .bind(opts.media_id.0)
        .fetch_optional(pool)
        .await?;

        if let Some(stream_id) = existing {
            let _ = link_stream_to_media(pool, StreamId(stream_id), opts.media_id).await;
            return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
        }
    }

    let stream_id = insert_base_stream(pool, &stream.base, StreamType::Http).await?;

    let behavior_hints_json = stream.behavior_hints.as_ref().map(|v| v.to_string());

    sqlx::query(
        "INSERT INTO http_stream (stream_id, url, format, behavior_hints, drm_key_id, drm_key, extractor_name) \
         VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)",
    )
    .bind(stream_id.0)
    .bind(&stream.url)
    .bind(&stream.format)
    .bind(behavior_hints_json.as_deref())
    .bind(&stream.drm_key_id)
    .bind(&stream.drm_key)
    .bind(&stream.extractor_name)
    .execute(pool)
    .await?;

    if opts.media_id.0 > 0 {
        link_stream_to_media_with_flags(
            pool,
            stream_id,
            opts.media_id,
            opts.is_primary,
            opts.is_verified,
        )
        .await?;
    }

    link_stream_parsed_metadata(pool, stream_id, &stream.base).await;

    Ok(StoreStreamResult::Inserted(stream_id))
}

/// Store a YouTube stream.
pub async fn store_youtube_stream(
    pool: &PgPool,
    stream: &YoutubeStoreInput,
    opts: &StoreStreamOpts,
) -> Result<StoreStreamResult, sqlx::Error> {
    if opts.media_id.0 > 0 {
        let existing: Option<i32> = sqlx::query_scalar(
            "SELECT ys.stream_id FROM youtube_stream ys \
             JOIN stream_media_link sml ON sml.stream_id = ys.stream_id \
             WHERE ys.video_id = $1 AND sml.media_id = $2 LIMIT 1",
        )
        .bind(&stream.video_id)
        .bind(opts.media_id.0)
        .fetch_optional(pool)
        .await?;

        if let Some(stream_id) = existing {
            return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
        }
    }

    let stream_id = insert_base_stream(pool, &stream.base, StreamType::Youtube).await?;

    sqlx::query(
        "INSERT INTO youtube_stream (stream_id, video_id, channel_id, channel_name, duration_seconds, is_live, is_premiere) \
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(stream_id.0)
    .bind(&stream.video_id)
    .bind(&stream.channel_id)
    .bind(&stream.channel_name)
    .bind(stream.duration_seconds)
    .bind(stream.is_live)
    .bind(stream.is_premiere)
    .execute(pool)
    .await?;

    if opts.media_id.0 > 0 {
        link_stream_to_media_with_flags(
            pool,
            stream_id,
            opts.media_id,
            opts.is_primary,
            opts.is_verified,
        )
        .await?;
    }

    Ok(StoreStreamResult::Inserted(stream_id))
}

/// Store an AceStream stream.
pub async fn store_acestream_stream(
    pool: &PgPool,
    stream: &AcestreamStoreInput,
    opts: &StoreStreamOpts,
) -> Result<StoreStreamResult, sqlx::Error> {
    if opts.media_id.0 > 0 {
        let existing: Option<i32> = if let Some(ref hash) = stream.info_hash {
            sqlx::query_scalar(
                "SELECT ace.stream_id FROM acestream_stream ace \
                 JOIN stream_media_link sml ON sml.stream_id = ace.stream_id \
                 WHERE ace.info_hash = $1 AND sml.media_id = $2 LIMIT 1",
            )
            .bind(hash)
            .bind(opts.media_id.0)
            .fetch_optional(pool)
            .await?
        } else {
            sqlx::query_scalar(
                "SELECT ace.stream_id FROM acestream_stream ace \
                 JOIN stream_media_link sml ON sml.stream_id = ace.stream_id \
                 WHERE ace.content_id = $1 AND sml.media_id = $2 LIMIT 1",
            )
            .bind(&stream.content_id)
            .bind(opts.media_id.0)
            .fetch_optional(pool)
            .await?
        };

        if let Some(stream_id) = existing {
            return Ok(StoreStreamResult::AlreadyExists(StreamId(stream_id)));
        }
    }

    let stream_id = insert_base_stream(pool, &stream.base, StreamType::Acestream).await?;

    sqlx::query(
        "INSERT INTO acestream_stream (stream_id, content_id, info_hash) VALUES ($1, $2, $3)",
    )
    .bind(stream_id.0)
    .bind(&stream.content_id)
    .bind(&stream.info_hash)
    .execute(pool)
    .await?;

    if opts.media_id.0 > 0 {
        link_stream_to_media_with_flags(
            pool,
            stream_id,
            opts.media_id,
            opts.is_primary,
            opts.is_verified,
        )
        .await?;
    }

    Ok(StoreStreamResult::Inserted(stream_id))
}

/// Insert or update per-file rows for an existing torrent (metadata enrichment path).
pub async fn upsert_torrent_files_by_hash(
    pool: &PgPool,
    info_hash: &str,
    files: &[super::streams::TorrentFileEntry],
    link_source: LinkSource,
) -> Result<(), sqlx::Error> {
    if files.is_empty() {
        return Ok(());
    }

    let row: Option<(StreamId, MediaId)> = sqlx::query_as(
        r#"
        SELECT ts.stream_id, sml.media_id
        FROM torrent_stream ts
        JOIN stream_media_link sml ON sml.stream_id = ts.stream_id
        WHERE ts.info_hash = $1
        LIMIT 1
        "#,
    )
    .bind(info_hash)
    .fetch_optional(pool)
    .await?;

    let (stream_id, media_id) = match row {
        Some(r) => r,
        None => return Ok(()),
    };

    let existing: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM stream_file WHERE stream_id = $1")
        .bind(stream_id.0)
        .fetch_one(pool)
        .await?;

    if existing > 0 {
        return Ok(());
    }

    let mut txn = pool.begin().await?;
    let total_size: i64 = files.iter().map(|f| f.size).sum();

    for f in files {
        let normalized = StreamFileStoreInput {
            file_index: f.file_index,
            filename: f.filename.clone(),
            size: Some(f.size),
            season_number: f.season.unwrap_or(0),
            episode_number: f.episode.unwrap_or(0),
        };
        if let Some(file_id) =
            insert_stream_file(&mut *txn, stream_id, &normalized, f.size > 0).await?
        {
            if let (Some(s), Some(e)) = (f.season, f.episode) {
                insert_file_media_link(&mut *txn, file_id, media_id, s, e, None, false, link_source)
                    .await?;
            }
        }
    }

    sqlx::query(
        "UPDATE torrent_stream SET total_size = GREATEST(total_size, $2), updated_at = NOW() WHERE stream_id = $1",
    )
    .bind(stream_id.0)
    .bind(total_size)
    .execute(&mut *txn)
    .await?;

    txn.commit().await?;
    Ok(())
}

async fn link_stream_parsed_metadata(pool: &PgPool, stream_id: StreamId, base: &StreamStoreBase) {
    use super::stream_links::{
        link_stream_audio_channels, link_stream_audio_formats, link_stream_hdr_formats,
        link_stream_languages,
    };

    let sid = stream_id.0;
    if !base.languages.is_empty() {
        let _ = link_stream_languages(pool, sid, &base.languages).await;
    }
    if !base.hdr_formats.is_empty() {
        let _ = link_stream_hdr_formats(pool, sid, &base.hdr_formats).await;
    }
    if !base.audio_formats.is_empty() {
        let _ = link_stream_audio_formats(pool, sid, &base.audio_formats).await;
    }
    if !base.audio_channels.is_empty() {
        let _ = link_stream_audio_channels(pool, sid, &base.audio_channels).await;
    }
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

async fn insert_base_stream(
    pool: &PgPool,
    base: &StreamStoreBase,
    stream_type: StreamType,
) -> Result<StreamId, sqlx::Error> {
    let name = strip_nul(&base.name);
    let source = strip_nul(&base.source);

    let stream_id: i32 = sqlx::query_scalar(
        r#"
        INSERT INTO stream (
            stream_type, name, source,
            uploader, uploader_user_id,
            resolution, codec, quality, bit_depth, release_group,
            is_proper, is_repack, is_extended, is_complete, is_dubbed,
            is_subbed, is_remastered, is_upscaled,
            is_active, is_blocked, is_public, playback_count,
            created_at
        ) VALUES (
            $1, $2, $3,
            $4, $5,
            $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15,
            $16, $17, $18,
            $19, $20, $21, 0,
            NOW()
        )
        RETURNING id
        "#,
    )
    .bind(stream_type)
    .bind(name.as_ref())
    .bind(source.as_ref())
    .bind(base.uploader.as_deref().map(|s| strip_nul(s).into_owned()))
    .bind(base.uploader_user_id)
    .bind(&base.resolution)
    .bind(&base.codec)
    .bind(&base.quality)
    .bind(&base.bit_depth)
    .bind(&base.release_group)
    .bind(base.is_proper)
    .bind(base.is_repack)
    .bind(base.is_extended)
    .bind(base.is_complete)
    .bind(base.is_dubbed)
    .bind(base.is_subbed)
    .bind(base.is_remastered)
    .bind(base.is_upscaled)
    .bind(base.is_active)
    .bind(base.is_blocked)
    .bind(base.is_public)
    .fetch_one(pool)
    .await?;

    Ok(StreamId(stream_id))
}

async fn link_torrent_to_media(
    pool: &PgPool,
    stream_id: StreamId,
    files: &[StreamFileStoreInput],
    opts: &StoreStreamOpts,
) -> Result<(), sqlx::Error> {
    link_files_or_media(pool, stream_id, files, opts).await
}

async fn link_files_or_media(
    pool: &PgPool,
    stream_id: StreamId,
    files: &[StreamFileStoreInput],
    opts: &StoreStreamOpts,
) -> Result<(), sqlx::Error> {
    if opts.media_id.0 <= 0 {
        return Ok(());
    }

    if opts.media_type == MediaType::Series {
        if !files.is_empty() {
            for f in files {
                if let Some(file_id) = insert_stream_file(pool, stream_id, f, false).await? {
                    let (season, episode) = if f.season_number > 0 && f.episode_number > 0 {
                        (f.season_number, f.episode_number)
                    } else {
                        super::stream_model::resolve_series_episode_numbers(
                            f.file_index,
                            None,
                            None,
                        )
                    };
                    insert_file_media_link(
                        pool,
                        file_id,
                        opts.media_id,
                        season,
                        episode,
                        opts.episode_end,
                        opts.is_primary,
                        opts.link_source,
                    )
                    .await?;
                }
            }
            return Ok(());
        }

        // Series pack / single-file torrent without per-episode breakdown: link at
        // stream level, and add file_media_link when the request specifies S/E.
        link_stream_to_media_with_flags(
            pool,
            stream_id,
            opts.media_id,
            opts.is_primary,
            opts.is_verified,
        )
        .await?;

        if let (Some(s_num), Some(e_num)) = (opts.season, opts.episode) {
            link_synthetic_episode_file(pool, stream_id, opts.media_id, s_num, e_num, opts).await?;
        }
        return Ok(());
    }

    link_stream_to_media_with_flags(
        pool,
        stream_id,
        opts.media_id,
        opts.is_primary,
        opts.is_verified,
    )
    .await?;

    Ok(())
}

/// Upsert a `stream_file` row for user/multi-file import (includes size).
pub async fn upsert_stream_file_row(
    pool: &PgPool,
    stream_id: StreamId,
    file: &StreamFileStoreInput,
) -> Result<Option<i32>, sqlx::Error> {
    insert_stream_file(pool, stream_id, file, true).await
}

/// Link a file to a media row at a specific season/episode.
pub async fn link_file_to_media_episode(
    pool: &PgPool,
    file_id: i32,
    media_id: MediaId,
    season: i32,
    episode: i32,
    link_source: LinkSource,
    is_primary: bool,
) -> Result<(), sqlx::Error> {
    insert_file_media_link(
        pool,
        file_id,
        media_id,
        season,
        episode,
        None,
        is_primary,
        link_source,
    )
    .await
}

async fn insert_stream_file<'e, E>(
    executor: E,
    stream_id: StreamId,
    file: &StreamFileStoreInput,
    with_size: bool,
) -> Result<Option<i32>, sqlx::Error>
where
    E: sqlx::Executor<'e, Database = sqlx::Postgres>,
{
    let filename = strip_nul(&file.filename);
    let file_id: Option<i32> = if with_size {
        sqlx::query_scalar(
            r#"
            INSERT INTO stream_file (stream_id, file_index, filename, size, file_type, is_archive)
            VALUES ($1, $2, $3, $4, $5, false)
            ON CONFLICT (stream_id, file_index) DO UPDATE SET filename = EXCLUDED.filename
            RETURNING id
            "#,
        )
        .bind(stream_id.0)
        .bind(file.file_index)
        .bind(filename.as_ref())
        .bind(file.size.unwrap_or(0))
        .bind(FileType::Video)
        .fetch_optional(executor)
        .await?
    } else {
        sqlx::query_scalar(
            r#"
            INSERT INTO stream_file (stream_id, file_index, filename, file_type, is_archive)
            VALUES ($1, $2, $3, $4, false)
            ON CONFLICT (stream_id, file_index) DO UPDATE SET is_archive = EXCLUDED.is_archive
            RETURNING id
            "#,
        )
        .bind(stream_id.0)
        .bind(file.file_index)
        .bind(filename.as_ref())
        .bind(FileType::Video)
        .fetch_optional(executor)
        .await?
    };
    Ok(file_id)
}

async fn insert_file_media_link<'e, E>(
    executor: E,
    file_id: i32,
    media_id: MediaId,
    season: i32,
    episode: i32,
    episode_end: Option<i32>,
    is_primary: bool,
    link_source: LinkSource,
) -> Result<(), sqlx::Error>
where
    E: sqlx::Executor<'e, Database = sqlx::Postgres>,
{
    sqlx::query(
        r#"
        INSERT INTO file_media_link
            (file_id, media_id, season_number, episode_number, episode_end,
             is_primary, confidence, link_source, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, 1.0, $7, NOW())
        ON CONFLICT (file_id, media_id, season_number, episode_number) DO NOTHING
        "#,
    )
    .bind(file_id)
    .bind(media_id)
    .bind(season)
    .bind(episode)
    .bind(episode_end)
    .bind(is_primary)
    .bind(link_source)
    .execute(executor)
    .await?;
    Ok(())
}

async fn link_synthetic_episode_file(
    pool: &PgPool,
    stream_id: StreamId,
    media_id: MediaId,
    season: i32,
    episode: i32,
    opts: &StoreStreamOpts,
) -> Result<(), sqlx::Error> {
    let file = StreamFileStoreInput {
        file_index: 0,
        filename: String::new(),
        size: None,
        season_number: season,
        episode_number: episode,
    };
    if let Some(file_id) = insert_stream_file(pool, stream_id, &file, false).await? {
        insert_file_media_link(
            pool,
            file_id,
            media_id,
            season,
            episode,
            opts.episode_end,
            opts.is_primary,
            opts.link_source,
        )
        .await?;
    }
    Ok(())
}

async fn cleanup_orphan_stream(pool: &PgPool, stream_id: StreamId) {
    let _ = sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id.0)
        .execute(pool)
        .await;
}

fn sanitize_base(base: &StreamStoreBase) -> StreamStoreBase {
    let mut b = base.clone();
    b.name = strip_nul(&b.name).into_owned();
    b.source = strip_nul(&b.source).into_owned();
    b
}

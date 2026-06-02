use sqlx::PgPool;

use super::types::{MediaId, StreamId};

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
pub async fn link_stream_audio_formats(
    pool: &PgPool,
    stream_id: i32,
    formats: &[String],
) -> Result<(), sqlx::Error> {
    for name in formats {
        if name.is_empty() {
            continue;
        }
        let fmt_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO audio_format(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        )
        .bind(name)
        .fetch_optional(pool)
        .await?;
        if let Some(fid) = fmt_id {
            sqlx::query(
                "INSERT INTO stream_audio_link(stream_id, audio_format_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(stream_id)
            .bind(fid)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

/// Link HDR format names to a stream.
pub async fn link_stream_hdr_formats(
    pool: &PgPool,
    stream_id: i32,
    formats: &[String],
) -> Result<(), sqlx::Error> {
    for name in formats {
        if name.is_empty() {
            continue;
        }
        let hdr_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO hdr_format(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        )
        .bind(name)
        .fetch_optional(pool)
        .await?;
        if let Some(hdr_id) = hdr_id {
            sqlx::query(
                "INSERT INTO stream_hdr_link(stream_id, hdr_format_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(stream_id)
            .bind(hdr_id)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

/// Link audio channel names to a stream.
pub async fn link_stream_audio_channels(
    pool: &PgPool,
    stream_id: i32,
    channels: &[String],
) -> Result<(), sqlx::Error> {
    for name in channels {
        if name.is_empty() {
            continue;
        }
        let ch_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO audio_channel(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        )
        .bind(name)
        .fetch_optional(pool)
        .await?;
        if let Some(ch_id) = ch_id {
            sqlx::query(
                "INSERT INTO stream_channel_link(stream_id, channel_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(stream_id)
            .bind(ch_id)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

/// Link audio languages to a stream.
pub async fn link_stream_languages(
    pool: &PgPool,
    stream_id: i32,
    languages: &[String],
) -> Result<(), sqlx::Error> {
    for lang in languages {
        if lang.is_empty() {
            continue;
        }
        let lang_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO language(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        )
        .bind(lang)
        .fetch_optional(pool)
        .await?;
        if let Some(lid) = lang_id {
            sqlx::query(
                "INSERT INTO stream_language_link(stream_id, language_id, language_type) VALUES($1, $2, 'AUDIO') ON CONFLICT DO NOTHING",
            )
            .bind(stream_id)
            .bind(lid)
            .execute(pool)
            .await?;
        }
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

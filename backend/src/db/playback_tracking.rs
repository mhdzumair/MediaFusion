use std::collections::HashMap;

use serde_json::Value;
use sqlx::PgPool;

/// Distinct logged-in users who played + anonymous play counter on `stream`.
pub async fn fetch_watched_counts_bulk(
    pool: &PgPool,
    stream_ids: &[i32],
) -> Result<HashMap<i32, i64>, sqlx::Error> {
    if stream_ids.is_empty() {
        return Ok(HashMap::new());
    }

    let rows: Vec<(i32, i64)> = sqlx::query_as(
        r#"SELECT s.id,
                  (s.playback_count + COALESCE(COUNT(DISTINCT pt.user_id), 0))::bigint AS watched_count
           FROM stream s
           LEFT JOIN playback_tracking pt
             ON pt.stream_id = s.id AND pt.user_id IS NOT NULL
           WHERE s.id = ANY($1)
           GROUP BY s.id, s.playback_count"#,
    )
    .bind(stream_ids)
    .fetch_all(pool)
    .await?;

    Ok(rows.into_iter().collect())
}

pub async fn fetch_watched_count(pool: &PgPool, stream_id: i32) -> i64 {
    fetch_watched_counts_bulk(pool, &[stream_id])
        .await
        .ok()
        .and_then(|m| m.get(&stream_id).copied())
        .unwrap_or(0)
}

/// Record a playback event. Authenticated users update `playback_tracking`; anonymous users
/// increment `stream.playback_count` (legacy Python behaviour).
pub async fn track_stream_playback(
    pool: &PgPool,
    stream_id: i32,
    media_id: i32,
    user_id: Option<i32>,
    profile_id: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    provider_name: Option<&str>,
    provider_service: Option<&str>,
) -> Result<(), sqlx::Error> {
    if let Some(uid) = user_id {
        let existing: Option<i32> = sqlx::query_scalar(
            r#"SELECT id FROM playback_tracking
               WHERE user_id = $1 AND stream_id = $2
                 AND season IS NOT DISTINCT FROM $3
                 AND episode IS NOT DISTINCT FROM $4
               LIMIT 1"#,
        )
        .bind(uid)
        .bind(stream_id)
        .bind(season)
        .bind(episode)
        .fetch_optional(pool)
        .await?;

        if let Some(id) = existing {
            sqlx::query(
                r#"UPDATE playback_tracking
                   SET last_played_at = NOW(),
                       play_count = play_count + 1,
                       provider_name = COALESCE($2, provider_name),
                       provider_service = COALESCE($3, provider_service)
                   WHERE id = $1"#,
            )
            .bind(id)
            .bind(provider_name)
            .bind(provider_service)
            .execute(pool)
            .await?;
        } else {
            sqlx::query(
                r#"INSERT INTO playback_tracking
                   (user_id, profile_id, stream_id, media_id, season, episode,
                    provider_name, provider_service, first_played_at, last_played_at, play_count)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW(), 1)"#,
            )
            .bind(uid)
            .bind(profile_id)
            .bind(stream_id)
            .bind(media_id)
            .bind(season)
            .bind(episode)
            .bind(provider_name)
            .bind(provider_service)
            .execute(pool)
            .await?;
        }
    } else {
        sqlx::query("UPDATE stream SET playback_count = playback_count + 1 WHERE id = $1")
            .bind(stream_id)
            .execute(pool)
            .await?;
    }

    Ok(())
}

pub async fn resolve_stream_id_from_info_hash(
    pool: &PgPool,
    info_hash: &str,
) -> Option<(i32, i32)> {
    sqlx::query_as::<_, (i32, i32)>(
        r#"SELECT ts.stream_id,
                  COALESCE(
                      (SELECT sml.media_id FROM stream_media_link sml
                       WHERE sml.stream_id = ts.stream_id AND sml.is_primary = true LIMIT 1),
                      (SELECT fml.media_id FROM stream_file sf
                       JOIN file_media_link fml ON fml.file_id = sf.id
                       WHERE sf.stream_id = ts.stream_id LIMIT 1)
                  ) AS media_id
           FROM torrent_stream ts
           WHERE ts.info_hash = $1
           LIMIT 1"#,
    )
    .bind(info_hash)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
    .filter(|(_, media_id)| *media_id > 0)
}

/// Resolve stream_id (and media_id) from watch-history `stream_info` JSON.
pub async fn resolve_stream_id_for_tracking(
    pool: &PgPool,
    stream_info: &Value,
    media_id: i32,
) -> Option<(i32, i32)> {
    if let Some(id) = stream_info.get("id").and_then(|v| v.as_i64()) {
        return Some((id as i32, media_id));
    }
    if let Some(hash) = stream_info.get("info_hash").and_then(|v| v.as_str())
        && let Some((stream_id, linked_media_id)) =
            resolve_stream_id_from_info_hash(pool, hash).await
    {
        return Some((stream_id, linked_media_id));
    }
    None
}

/// Fire-and-forget playback counter after a successful debrid playback resolve.
pub fn spawn_track_stream_playback_by_hash(
    pool: PgPool,
    info_hash: String,
    user_id: Option<i32>,
    profile_id: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    provider_name: Option<String>,
    provider_service: Option<String>,
) {
    tokio::spawn(async move {
        let Some((stream_id, media_id)) = resolve_stream_id_from_info_hash(&pool, &info_hash).await
        else {
            return;
        };
        if let Err(e) = track_stream_playback(
            &pool,
            stream_id,
            media_id,
            user_id,
            profile_id,
            season,
            episode,
            provider_name.as_deref(),
            provider_service.as_deref(),
        )
        .await
        {
            tracing::warn!("track_stream_playback failed stream_id={stream_id}: {e}");
        }
    });
}

/// Post-playback torrent stream metadata update.
///
/// When a user plays a torrent via a debrid provider and the DB has no file-level
/// metadata (stream_file rows), we save the file list returned by the provider so
/// future users see the correct streams without waiting for the debrid API.
///
/// Mirrors Python `update_torrent_streams_metadata` including is_blocked and
/// annotation request paths when episode parsing fails.
use fred::prelude::{Expiration, KeysInterface};
use sqlx::PgPool;
use tracing::{debug, info, warn};

use crate::{
    db::{
        MediaId,
        streams::{TorrentFileEntry, upsert_stream_files},
        upsert_series_episode,
    },
    parser::episode_detector::{detect_episode, is_video_file},
};

const ANNOTATION_LOCK_PREFIX: &str = "annotation_lock_";
const ANNOTATION_LOCK_TTL: i64 = 259200; // 3 days

type RacingFileRow = (i32, MediaId, String, Option<i32>, Option<i32>);

/// A file entry returned by a debrid provider.
#[derive(Debug, Clone)]
pub struct ProviderFile {
    /// Zero-based index as reported by the provider.
    pub file_index: i32,
    /// File path or name (base name or full path within the torrent).
    pub path: String,
    /// File size in bytes.
    pub bytes: i64,
}

async fn annotation_lock_acquired(redis: &fred::clients::Client, info_hash: &str) -> bool {
    let key = format!("{ANNOTATION_LOCK_PREFIX}{info_hash}");
    if redis.exists::<i64, _>(&key).await.unwrap_or(0) > 0 {
        return false;
    }
    redis
        .set::<(), _, _>(
            &key,
            "1",
            Some(Expiration::EX(ANNOTATION_LOCK_TTL)),
            None,
            false,
        )
        .await
        .is_ok()
}

async fn block_stream(pool: &PgPool, info_hash: &str) {
    if let Err(e) = sqlx::query(
        "UPDATE stream SET is_blocked = true, updated_at = NOW() \
         FROM torrent_stream ts WHERE ts.stream_id = stream.id AND ts.info_hash = $1",
    )
    .bind(info_hash)
    .execute(pool)
    .await
    {
        warn!("metadata_update: block_stream {info_hash}: {e}");
    }
}

async fn stream_name_for_hash(pool: &PgPool, info_hash: &str) -> Option<String> {
    sqlx::query_scalar(
        "SELECT s.name FROM stream s \
         JOIN torrent_stream ts ON ts.stream_id = s.id \
         WHERE ts.info_hash = $1 LIMIT 1",
    )
    .bind(info_hash)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

/// Re-apply racing filename parsing and sync series episode rows after playback.
/// Cheap to run on every series play — keeps the episode list current when the
/// parser improves or files were partially mapped on a prior import.
pub async fn refresh_racing_episode_metadata(pool: &PgPool, info_hash: &str, season: Option<i32>) {
    let stream_name = stream_name_for_hash(pool, info_hash).await;
    if stream_name
        .as_deref()
        .is_some_and(|n| crate::parser::parse_racing_title(n).is_some())
        && let Some(default_season) = season
    {
        remap_unmapped_racing_files(pool, info_hash, default_season).await;
        sync_series_episodes_for_hash(pool, info_hash).await;
    }
}

/// Update stream_file / file_media_link rows from the provider's file list.
///
/// Every video file the provider reports gets stored — even ones we can't
/// assign a season/episode to — so a partially-mapped torrent (e.g. a
/// scraper stub covering only one episode) ends up with its *complete* real
/// file list in the DB. Files we can't auto-map are still stored (with no
/// `file_media_link`) so they show up for manual annotation instead of being
/// silently dropped.
///
/// - `season`: `None` → movie (pick largest video file only)
/// - `season`: `Some(s)` → series (detect episode in each file, link all)
pub async fn update_metadata(
    pool: &PgPool,
    redis: Option<&fred::clients::Client>,
    info_hash: &str,
    files: &[ProviderFile],
    season: Option<i32>,
) {
    if let Some(r) = redis
        && !annotation_lock_acquired(r, info_hash).await
    {
        debug!("metadata_update: skip {info_hash} — recent annotation lock");
        return;
    }

    let video_files: Vec<&ProviderFile> = files
        .iter()
        .filter(|f| {
            let base = std::path::Path::new(&f.path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&f.path);
            is_video_file(base)
        })
        .collect();

    if video_files.is_empty() {
        debug!("metadata_update: no video files for {info_hash} — blocking");
        block_stream(pool, info_hash).await;
        return;
    }

    let stream_name = if season.is_some() {
        stream_name_for_hash(pool, info_hash).await
    } else {
        None
    };

    let mut any_unmapped = false;

    let entries: Vec<TorrentFileEntry> = match season {
        None => {
            let largest = video_files
                .iter()
                .max_by_key(|f| f.bytes)
                .expect("non-empty");
            let base = std::path::Path::new(&largest.path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&largest.path)
                .to_string();
            vec![TorrentFileEntry {
                file_index: largest.file_index,
                filename: base,
                size: largest.bytes,
                season: None,
                episode: None,
            }]
        }
        Some(default_season) => {
            // Race-weekend torrents (Formula/MotoGP) label sessions by name
            // (FP1/FP2/Qualifying/Race) rather than SxxExx, so the generic
            // numeric detector below never matches them. Only fall back to
            // the racing-session keyword matcher when the release title
            // itself is confirmed to be a racing release — its keyword
            // matching is a bare substring check (e.g. "race"), which would
            // misfire on ordinary titles (e.g. "Grace") without this guard.
            let is_racing_release = stream_name
                .as_deref()
                .is_some_and(|name| crate::parser::parse_racing_title(name).is_some());

            video_files
                .iter()
                .map(|f| {
                    let base = std::path::Path::new(&f.path)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or(&f.path);
                    let ep = if is_racing_release {
                        crate::parser::racing_file_episode(base)
                            .map(|(episode, _)| (default_season, episode))
                            .or_else(|| {
                                detect_episode(base, default_season).map(|e| (e.season, e.episode))
                            })
                    } else {
                        detect_episode(base, default_season).map(|e| (e.season, e.episode))
                    };
                    if ep.is_none() {
                        any_unmapped = true;
                    }
                    TorrentFileEntry {
                        file_index: f.file_index,
                        filename: base.to_string(),
                        size: f.bytes,
                        season: ep.map(|(s, _)| s),
                        episode: ep.map(|(_, e)| e),
                    }
                })
                .collect()
        }
    };

    match upsert_stream_files(pool, info_hash, &entries).await {
        Ok(()) => info!(
            "metadata_update: stored {} files for {info_hash}",
            entries.len()
        ),
        Err(e) => {
            warn!("metadata_update: DB error for {info_hash}: {e}");
            return;
        }
    }

    if any_unmapped {
        if let Some(name) = &stream_name {
            crate::util::notification_registry::send_file_annotation_request(info_hash, name).await;
        }
        info!("metadata_update: {info_hash} has unmapped files — requested annotation");
    }

    sync_series_episodes_for_hash(pool, info_hash).await;

    if stream_name
        .as_deref()
        .is_some_and(|n| crate::parser::parse_racing_title(n).is_some())
        && let Some(default_season) = season
    {
        remap_unmapped_racing_files(pool, info_hash, default_season).await;
        sync_series_episodes_for_hash(pool, info_hash).await;
    }
}

/// Upsert `season`/`episode` metadata rows for every mapped file on this torrent
/// so the series episode list stays in sync with `file_media_link`.
async fn sync_series_episodes_for_hash(pool: &PgPool, info_hash: &str) {
    let rows: Vec<(i32, i32, i32, String)> = match sqlx::query_as(
        "SELECT sml.media_id, fml.season_number, fml.episode_number, sf.filename \
         FROM torrent_stream ts \
         JOIN stream_media_link sml ON sml.stream_id = ts.stream_id \
         JOIN stream_file sf ON sf.stream_id = ts.stream_id \
         JOIN file_media_link fml ON fml.file_id = sf.id AND fml.media_id = sml.media_id \
         WHERE ts.info_hash = $1",
    )
    .bind(info_hash)
    .fetch_all(pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!("metadata_update: sync_series_episodes_for_hash {info_hash}: {e}");
            return;
        }
    };

    for (media_id, season, episode, filename) in rows {
        let title = crate::parser::racing_file_episode(&filename)
            .map(|(_, t)| t)
            .unwrap_or(filename);
        if let Err(e) =
            upsert_series_episode(pool, MediaId(media_id), season, episode, &title).await
        {
            warn!(
                "metadata_update: upsert_series_episode media_id={media_id} s{season}e{episode}: {e}"
            );
        }
    }
}

/// Re-apply racing filename parsing to every stored file on this torrent —
/// fixes both unmapped files and links that used the wrong episode slot
/// (e.g. Practice assigned to episode 5 via sequential import fallback).
async fn remap_unmapped_racing_files(pool: &PgPool, info_hash: &str, default_season: i32) {
    let rows: Vec<RacingFileRow> = match sqlx::query_as(
        "SELECT sf.id, sml.media_id, sf.filename, fml.season_number, fml.episode_number \
         FROM torrent_stream ts \
         JOIN stream st ON st.id = ts.stream_id \
         JOIN stream_media_link sml ON sml.stream_id = st.id \
         JOIN stream_file sf ON sf.stream_id = st.id \
         LEFT JOIN file_media_link fml ON fml.file_id = sf.id AND fml.media_id = sml.media_id \
         WHERE ts.info_hash = $1",
    )
    .bind(info_hash)
    .fetch_all(pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!("metadata_update: remap_unmapped_racing_files {info_hash}: {e}");
            return;
        }
    };

    for (file_id, media_id, filename, linked_season, linked_episode) in rows {
        let Some((episode, _)) = crate::parser::racing_file_episode(&filename) else {
            continue;
        };
        if linked_season == Some(default_season) && linked_episode == Some(episode) {
            continue;
        }

        if let Err(e) =
            sqlx::query("DELETE FROM file_media_link WHERE file_id = $1 AND media_id = $2")
                .bind(file_id)
                .bind(media_id.0)
                .execute(pool)
                .await
        {
            warn!("metadata_update: clear wrong link file_id={file_id}: {e}");
            continue;
        }

        // Drop any other file still claiming this episode slot.
        sqlx::query(
            "DELETE FROM file_media_link fml \
             USING stream_file sf \
             WHERE fml.file_id = sf.id \
               AND sf.stream_id = (SELECT stream_id FROM torrent_stream WHERE info_hash = $1) \
               AND fml.media_id = $2 \
               AND fml.season_number = $3 \
               AND fml.episode_number = $4 \
               AND sf.id != $5",
        )
        .bind(info_hash)
        .bind(media_id.0)
        .bind(default_season)
        .bind(episode)
        .bind(file_id)
        .execute(pool)
        .await
        .ok();

        if let Err(e) = sqlx::query(
            "INSERT INTO file_media_link \
                (file_id, media_id, season_number, episode_number, is_primary, confidence, link_source, created_at) \
             VALUES ($1, $2, $3, $4, false, 1.0, $5, NOW()) \
             ON CONFLICT (file_id, media_id, season_number, episode_number) DO NOTHING",
        )
        .bind(file_id)
        .bind(media_id.0)
        .bind(default_season)
        .bind(episode)
        .bind(crate::db::LinkSource::TorrentMetadata)
        .execute(pool)
        .await
        {
            warn!("metadata_update: link remapped file_id={file_id}: {e}");
        }
    }
}

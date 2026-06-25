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
    db::streams::{TorrentFileEntry, upsert_stream_files},
    parser::episode_detector::{detect_episode, is_video_file},
};

const ANNOTATION_LOCK_PREFIX: &str = "annotation_lock_";
const ANNOTATION_LOCK_TTL: i64 = 259200; // 3 days

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

async fn store_annotation_files(pool: &PgPool, info_hash: &str, files: &[ProviderFile]) {
    let entries: Vec<TorrentFileEntry> = files
        .iter()
        .filter_map(|f| {
            let base = std::path::Path::new(&f.path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&f.path);
            if !is_video_file(base) {
                return None;
            }
            Some(TorrentFileEntry {
                file_index: f.file_index,
                filename: base.to_string(),
                size: f.bytes,
                season: None,
                episode: None,
            })
        })
        .collect();
    if entries.is_empty() {
        return;
    }
    match upsert_stream_files(pool, info_hash, &entries).await {
        Err(e) => {
            warn!("metadata_update: annotation files for {info_hash}: {e}");
        }
        _ => {
            info!(
                "metadata_update: stored {} annotation files for {info_hash} (manual linking needed)",
                entries.len()
            );
        }
    }
}

/// Update stream_file / file_media_link rows from the provider's file list.
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
        Some(default_season) => video_files
            .iter()
            .filter_map(|f| {
                let base = std::path::Path::new(&f.path)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or(&f.path);
                let ep = detect_episode(base, default_season)?;
                Some(TorrentFileEntry {
                    file_index: f.file_index,
                    filename: base.to_string(),
                    size: f.bytes,
                    season: Some(ep.season),
                    episode: Some(ep.episode),
                })
            })
            .collect(),
    };

    if entries.is_empty() {
        if season.is_some() {
            store_annotation_files(pool, info_hash, files).await;
            if let Some(name) = stream_name_for_hash(pool, info_hash).await {
                crate::util::notification_registry::send_file_annotation_request(info_hash, &name)
                    .await;
            }
            info!("metadata_update: requested annotation for {info_hash} season={season:?}");
        } else {
            block_stream(pool, info_hash).await;
        }
        return;
    }

    match upsert_stream_files(pool, info_hash, &entries).await {
        Ok(()) => info!(
            "metadata_update: stored {} files for {info_hash}",
            entries.len()
        ),
        Err(e) => warn!("metadata_update: DB error for {info_hash}: {e}"),
    }
}

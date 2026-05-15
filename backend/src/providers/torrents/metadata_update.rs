/// Post-playback torrent stream metadata update.
///
/// When a user plays a torrent via a debrid provider and the DB has no file-level
/// metadata (stream_file rows), we save the file list returned by the provider so
/// future users see the correct streams without waiting for the debrid API.
use sqlx::PgPool;
use tracing::{info, warn};

use crate::{
    db::streams::{upsert_stream_files, TorrentFileEntry},
    parser::episode_detector::{detect_episode, is_video_file},
};

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

/// Update stream_file / file_media_link rows from the provider's file list.
///
/// - `season`: `None` → movie (pick largest video file only)
/// - `season`: `Some(s)` → series (detect episode in each file, link all)
pub async fn update_metadata(
    pool: &PgPool,
    info_hash: &str,
    files: &[ProviderFile],
    season: Option<i32>,
) {
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
        warn!("metadata_update: no video files in provider response for {info_hash}");
        return;
    }

    let entries: Vec<TorrentFileEntry> = match season {
        None => {
            // Movie: pick the single largest video file
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
            // Series: detect episode in each video filename
            video_files
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
                .collect()
        }
    };

    if entries.is_empty() {
        info!("metadata_update: could not parse episodes for {info_hash} season={season:?}");
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

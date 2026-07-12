//! Shared Formula racing file-resolution helpers used by ext.to and RSS spiders.

use tracing::{debug, info};

use crate::{
    parser,
    scrapers::{StreamFile, torrent_metadata},
};

/// A video file row parsed from an ext.to `#torrent_files` HTML table.
#[derive(Debug, Clone)]
pub struct HtmlTorrentFile {
    pub file_index: i32,
    pub filename: String,
    #[allow(dead_code)]
    pub size: Option<i64>,
}

pub fn racing_files_from_html(html_files: &[HtmlTorrentFile]) -> Vec<StreamFile> {
    let mut files: Vec<StreamFile> = html_files
        .iter()
        .filter_map(|hf| {
            let (episode, title) = parser::racing_file_episode(&hf.filename).or_else(|| {
                Some((
                    hf.file_index + 1,
                    hf.filename
                        .rsplit('/')
                        .next()
                        .unwrap_or(&hf.filename)
                        .to_string(),
                ))
            })?;
            Some(StreamFile {
                file_index: hf.file_index,
                filename: title,
                season_number: 1,
                episode_number: episode,
            })
        })
        .collect();
    files.sort_by_key(|f| f.episode_number);
    files
}

pub fn racing_files_from_torrent_bytes(bytes: &[u8]) -> Vec<StreamFile> {
    let Some(parsed) = torrent_metadata::parse_torrent_bytes(bytes) else {
        return Vec::new();
    };
    let torrent = lava_torrent::torrent::v1::Torrent::read_from_bytes(bytes).ok();
    let file_entries = torrent
        .as_ref()
        .and_then(|t| t.files.as_ref())
        .map(|files| {
            files
                .iter()
                .enumerate()
                .filter_map(|(idx, f)| {
                    let path_str = f.path.to_string_lossy();
                    let base = std::path::Path::new(path_str.as_ref())
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or(path_str.as_ref());
                    if !parser::episode_detector::is_video_file(base) {
                        return None;
                    }
                    let (episode, title) = parser::racing_file_episode(base)?;
                    Some(StreamFile {
                        file_index: idx as i32,
                        filename: title,
                        season_number: 1,
                        episode_number: episode,
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    if !file_entries.is_empty() {
        let mut files = file_entries;
        files.sort_by_key(|f| f.episode_number);
        info!(
            "formula_racing: parsed {} session file(s) from .torrent ({})",
            files.len(),
            parsed.info_hash
        );
        return files;
    }
    Vec::new()
}

/// Placeholder for magnet-only race releases with no resolvable file list yet.
pub fn unresolved_racing_placeholder(display_title: &str) -> Vec<StreamFile> {
    vec![StreamFile {
        file_index: -1,
        filename: display_title.to_string(),
        season_number: 1,
        episode_number: 1,
    }]
}

async fn racing_files_already_resolved(pool: &sqlx::PgPool, info_hash: &str) -> bool {
    sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM stream_file sf \
         JOIN torrent_stream ts ON ts.stream_id = sf.stream_id \
         WHERE ts.info_hash = $1",
    )
    .bind(info_hash)
    .fetch_one(pool)
    .await
    .map(|c| c > 1)
    .unwrap_or(false)
}

/// Resolve bundled race-weekend session files from HTML, `.torrent` bytes, or DHT.
pub async fn resolve_racing_files(
    label: &str,
    info_hash: &str,
    html_files: &[HtmlTorrentFile],
    torrent_bytes: Option<&[u8]>,
    display_title: &str,
    pool: &sqlx::PgPool,
    proxy_url: Option<&str>,
) -> Vec<StreamFile> {
    if !html_files.is_empty() {
        let files = racing_files_from_html(html_files);
        if !files.is_empty() {
            info!(
                "{label}: HTML-resolved {} session file(s) for {info_hash}",
                files.len()
            );
            return files;
        }
    }

    if let Some(bytes) = torrent_bytes {
        let files = racing_files_from_torrent_bytes(bytes);
        if !files.is_empty() {
            return files;
        }
    }

    if racing_files_already_resolved(pool, info_hash).await {
        return unresolved_racing_placeholder(display_title);
    }

    let meta =
        match crate::demagnetize::resolve(info_hash, std::time::Duration::from_secs(20), proxy_url)
            .await
        {
            Ok(m) => m,
            Err(e) => {
                debug!("{label}: demagnetize failed for {info_hash}: {e}");
                return unresolved_racing_placeholder(display_title);
            }
        };

    let mut files: Vec<StreamFile> = meta
        .files
        .iter()
        .enumerate()
        .filter_map(|(idx, f)| {
            let base = std::path::Path::new(&f.path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&f.path);
            if !parser::episode_detector::is_video_file(base) {
                return None;
            }
            let (episode, _) = parser::racing_file_episode(base)?;
            Some(StreamFile {
                file_index: idx as i32,
                filename: base.to_string(),
                season_number: 1,
                episode_number: episode,
            })
        })
        .collect();

    if files.is_empty() {
        debug!("{label}: no recognisable sessions in DHT file list for {info_hash}");
        return unresolved_racing_placeholder(display_title);
    }
    files.sort_by_key(|f| f.episode_number);
    info!(
        "{label}: DHT-resolved {} session file(s) for {info_hash}",
        files.len()
    );
    files
}

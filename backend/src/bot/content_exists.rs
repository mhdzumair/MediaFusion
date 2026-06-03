//! Pre-import duplicate detection (Python `_check_content_already_exists`).

use sha2::{Digest, Sha256};
use sqlx::PgPool;

use super::model::{ContentType, ConversationState};

pub async fn check_content_already_exists(
    pool: &PgPool,
    conv: &ConversationState,
) -> Option<String> {
    let analysis = conv.analysis_result.as_ref()?;
    let content_type = conv.content_type?;

    match content_type {
        ContentType::Magnet | ContentType::TorrentFile | ContentType::TorrentUrl => {
            let info_hash = analysis
                .get("info_hash")
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|s| !s.is_empty())?;
            let normalized = info_hash.to_lowercase();
            let exists: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM torrent_stream WHERE lower(info_hash) = $1)",
            )
            .bind(&normalized)
            .fetch_one(pool)
            .await
            .unwrap_or(false);
            if exists {
                return Some(format!("Torrent already exists (`{normalized}`)"));
            }
        }
        ContentType::Youtube => {
            let video_id = analysis
                .get("video_id")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())?;
            let exists: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM youtube_stream WHERE video_id = $1)",
            )
            .bind(video_id)
            .fetch_one(pool)
            .await
            .unwrap_or(false);
            if exists {
                return Some(format!("YouTube video already exists (`{video_id}`)"));
            }
        }
        ContentType::Http => {
            let url = analysis
                .get("url")
                .and_then(|v| v.as_str())
                .or_else(|| conv.raw_input.as_str())
                .filter(|s| !s.is_empty())?;
            let exists: bool =
                sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM http_stream WHERE url = $1)")
                    .bind(url)
                    .fetch_one(pool)
                    .await
                    .unwrap_or(false);
            if exists {
                return Some("HTTP stream URL already exists".to_string());
            }
        }
        ContentType::Nzb => {
            let nzb_url = analysis
                .get("nzb_url")
                .and_then(|v| v.as_str())
                .or_else(|| conv.raw_input.as_str())
                .filter(|s| !s.is_empty())?;
            let nzb_guid = sha256_prefix(nzb_url);
            let exists: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM usenet_stream WHERE nzb_guid = $1)",
            )
            .bind(&nzb_guid)
            .fetch_one(pool)
            .await
            .unwrap_or(false);
            if exists {
                return Some(format!("NZB already exists (`{nzb_guid}`)"));
            }
        }
        ContentType::Acestream => {
            let content_id = analysis
                .get("content_id")
                .and_then(|v| v.as_str())
                .or_else(|| conv.raw_input.as_str())
                .filter(|s| !s.is_empty())?;
            let exists: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM acestream_stream WHERE content_id = $1)",
            )
            .bind(content_id)
            .fetch_one(pool)
            .await
            .unwrap_or(false);
            if exists {
                return Some(format!("AceStream already exists (`{content_id}`)"));
            }
        }
        ContentType::Video => {
            let file_unique_id = conv
                .raw_input
                .get("file_unique_id")
                .and_then(|v| v.as_str())
                .or_else(|| analysis.get("file_unique_id").and_then(|v| v.as_str()))
                .filter(|s| !s.is_empty())?;
            let exists: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM telegram_stream WHERE file_unique_id = $1)",
            )
            .bind(file_unique_id)
            .fetch_one(pool)
            .await
            .unwrap_or(false);
            if exists {
                return Some("Telegram file already exists (same file_unique_id)".to_string());
            }
        }
    }

    None
}

fn sha256_prefix(input: &str) -> String {
    let digest = Sha256::digest(input.as_bytes());
    digest[..16].iter().map(|b| format!("{b:02x}")).collect()
}

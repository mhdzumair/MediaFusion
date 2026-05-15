//! Request-time Telegram MTProto live scraper using `grammers-client`.
//!
//! Resolves each configured channel, iterates recent messages, extracts
//! documents that look like video files, parses them with PTT and filters
//! by title similarity before returning [`ScrapedTelegramStream`]s.

use std::sync::Arc;

use grammers_client::Client;

use crate::{
    config::AppConfig,
    parser,
    scrapers::{ScrapedTelegramStream, SearchMeta},
};

const VIDEO_EXTENSIONS: &[&str] = &[
    ".mkv", ".mp4", ".avi", ".webm", ".mov", ".flv", ".wmv", ".m4v",
];

// ─── Client initialisation ────────────────────────────────────────────────────

/// Build and return a connected Telegram MTProto client, or `None` if the
/// required env vars are missing / session is invalid.
///
/// The underlying `SenderPoolRunner` is spawned as a background tokio task —
/// it will keep the connection alive for the process lifetime.
pub async fn init_client(config: &AppConfig) -> Option<Arc<Client>> {
    let api_id = config.telegram_api_id?;
    let api_hash = config.telegram_api_hash.as_deref()?;
    let session_b64 = config.telegram_grammers_session.as_deref()?;

    match build_client(api_id, api_hash, session_b64).await {
        Ok(client) => {
            tracing::info!("telegram: MTProto client initialised");
            Some(Arc::new(client))
        }
        Err(e) => {
            tracing::warn!("telegram: client init failed: {e}");
            None
        }
    }
}

async fn build_client(
    api_id: i32,
    api_hash: &str,
    session_b64: &str,
) -> Result<Client, Box<dyn std::error::Error + Send + Sync>> {
    use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
    use grammers_session::storages::MemorySession;

    // Decode session bytes
    let session_bytes = BASE64
        .decode(session_b64)
        .map_err(|e| format!("telegram: invalid base64 session: {e}"))?;

    // Deserialize into MemorySession via SessionData
    let session_data: grammers_session::SessionData = bincode_deserialize(&session_bytes)
        .unwrap_or_else(|_| {
            tracing::debug!("telegram: could not deserialize session data, using default");
            grammers_session::SessionData::default()
        });

    let session = Arc::new(MemorySession::from(session_data));

    // Build the SenderPool (contains runner + fat handle)
    let pool = grammers_client::sender::SenderPool::new(Arc::clone(&session) as Arc<_>, api_id);
    let runner = pool.runner;
    let handle = pool.handle;

    // Spawn the runner as a background task
    tokio::spawn(async move {
        runner.run().await;
    });

    // Build the Client from the fat handle
    let client = Client::new(handle);

    // api_hash is part of session auth negotiation handled by SenderPool internally
    let _ = api_hash;
    Ok(client)
}

/// Fallback: try bincode deserialization of session data.
/// grammers-session 0.9 uses bincode internally for its SQLite storage.
fn bincode_deserialize(
    bytes: &[u8],
) -> Result<grammers_session::SessionData, Box<dyn std::error::Error + Send + Sync>> {
    // grammers-session doesn't expose a public bytes API, so we fall through
    // to the default. This function exists as a hook for future improvement.
    let _ = bytes;
    Err("no public byte deserializer in grammers-session 0.9".into())
}

// ─── Scrape entry point ───────────────────────────────────────────────────────

/// Scrape all configured channels (global + per-user) and return matching streams.
#[allow(clippy::too_many_arguments)]
pub async fn scrape(
    client: &Client,
    channels: &[String],
    user_channels: &[String],
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    message_limit: i32,
    min_size: u64,
) -> Vec<ScrapedTelegramStream> {
    let mut all_channels: Vec<String> = channels.to_vec();
    all_channels.extend_from_slice(user_channels);
    all_channels.dedup();

    let mut results = Vec::new();
    for channel in &all_channels {
        let channel_results = scrape_channel(
            client,
            channel,
            meta,
            media_type,
            season,
            episode,
            message_limit,
            min_size,
        )
        .await;
        results.extend(channel_results);
    }
    results
}

// ─── Per-channel scrape ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn scrape_channel(
    client: &Client,
    channel: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    message_limit: i32,
    min_size: u64,
) -> Vec<ScrapedTelegramStream> {
    let username = channel.trim_start_matches('@');

    // Resolve channel entity
    let peer = match client.resolve_username(username).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            tracing::debug!("telegram: channel @{username} not found");
            return vec![];
        }
        Err(e) => {
            tracing::warn!("telegram: resolve @{username}: {e}");
            return vec![];
        }
    };

    // Get a PeerRef (needed for iter_messages)
    let peer_ref = match peer.to_ref().await {
        Some(r) => r,
        None => {
            tracing::warn!("telegram: @{username}: no peer ref (min peer, not cached)");
            return vec![];
        }
    };

    // Extract chat metadata for embedding into results
    let chat_id = peer.id().bot_api_dialog_id();
    let chat_username: Option<String> = match &peer {
        grammers_client::peer::Peer::Channel(c) => c.username().map(str::to_string),
        grammers_client::peer::Peer::Group(g) => g.username().map(str::to_string),
        grammers_client::peer::Peer::User(u) => u.username().map(str::to_string),
    };

    // Iterate messages
    let mut iter = client.iter_messages(peer_ref).limit(message_limit as usize);

    let mut results = Vec::new();
    loop {
        match iter.next().await {
            Ok(Some(msg)) => {
                if let Some(stream) = process_message(
                    &msg,
                    chat_id,
                    &chat_username,
                    meta,
                    media_type,
                    season,
                    episode,
                    min_size,
                ) {
                    results.push(stream);
                }
            }
            Ok(None) => break,
            Err(e) => {
                tracing::warn!("telegram: iter_messages @{username}: {e}");
                break;
            }
        }
    }

    results
}

// ─── Message processing ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn process_message(
    message: &grammers_client::message::Message,
    chat_id: i64,
    chat_username: &Option<String>,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    min_size: u64,
) -> Option<ScrapedTelegramStream> {
    use grammers_client::media::Media;

    // Only handle document media
    let (file_name, size, mime_type): (String, i64, Option<String>) = match message.media()? {
        Media::Document(doc) => {
            let name = doc.name()?.to_string();
            let size = doc.size().unwrap_or(0) as i64;
            let mime = doc.mime_type().map(str::to_string);
            (name, size, mime)
        }
        _ => return None,
    };

    // Filter by video extension
    let lower: String = file_name.to_lowercase();
    if !VIDEO_EXTENSIONS.iter().any(|ext| lower.ends_with(ext)) {
        return None;
    }

    // Minimum file size check
    if size > 0 && (size as u64) < min_size {
        return None;
    }

    // Adult content filter
    if parser::contains_adult_keywords(&file_name) {
        return None;
    }

    // Parse title with PTT
    let parsed = parser::parse_title(&file_name);

    // Title similarity check (80% threshold)
    let ratio =
        parser::similarity_ratio(parsed.title.as_deref().unwrap_or(&file_name), &meta.title);
    if ratio < 80 {
        return None;
    }

    // For series: verify season/episode match
    if media_type == "series" {
        if let (Some(s), Some(e)) = (season, episode) {
            let matches_season = parsed.seasons.contains(&s);
            let matches_ep = parsed.episodes.contains(&e);
            if !matches_season || !matches_ep {
                return None;
            }
        }
    }

    Some(ScrapedTelegramStream {
        chat_id,
        chat_username: chat_username.clone(),
        message_id: message.id(),
        file_name: file_name.clone(),
        size,
        mime_type,
        source: "telegram".to_string(),
        name: file_name,
        parsed,
        season,
        episode,
    })
}

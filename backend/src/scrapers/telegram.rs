//! Request-time Telegram MTProto live scraper using `grammers-client`.
//!
//! Resolves each configured channel, iterates recent messages, extracts
//! documents that look like video files, parses them with PTT and filters
//! by title similarity before returning [`ScrapedTelegramStream`]s.

use std::sync::Arc;

use grammers_client::Client;
use grammers_session::SessionData;
use regex::Regex;
use std::sync::OnceLock;

use crate::{
    config::AppConfig,
    parser,
    scrapers::{ScrapedTelegramStream, SearchMeta},
    state::KeywordFilterCache,
};

const VIDEO_EXTENSIONS: &[&str] = &[
    ".mkv", ".mp4", ".avi", ".webm", ".mov", ".flv", ".wmv", ".m4v",
];

fn imdb_pattern() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"tt\d{7,8}").expect("IMDB_PATTERN"))
}

fn extract_caption_imdb(caption: &str) -> Option<String> {
    imdb_pattern().find(caption).map(|m| m.as_str().to_string())
}

fn document_is_video(doc: &grammers_client::media::Document) -> bool {
    if doc.mime_type().is_some_and(|m| m.starts_with("video/")) {
        return true;
    }
    doc.duration().is_some() && doc.resolution().is_some()
}

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

    let session_data = match crate::util::telegram_session::parse_session_data(session_b64) {
        Ok(d) => d,
        Err(e) => {
            tracing::warn!("telegram: session parse failed: {e}");
            return None;
        }
    };

    if !crate::util::telegram_session::session_is_authenticated(&session_data) {
        tracing::warn!(
            "telegram: session is not authenticated — scraping will not work; \
             run `cargo run --bin telegram_session` or convert your Telethon session"
        );
        return None;
    }

    match build_client(api_id, api_hash, session_data).await {
        Ok(client) => {
            tracing::info!("telegram: MTProto client initialised (authenticated session loaded)");
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
    session_data: SessionData,
) -> Result<Client, Box<dyn std::error::Error + Send + Sync>> {
    use grammers_session::storages::MemorySession;
    use std::sync::Arc;

    let session = Arc::new(MemorySession::from(session_data));

    let pool = grammers_client::sender::SenderPool::new(Arc::clone(&session) as Arc<_>, api_id);
    let runner = pool.runner;
    let handle = pool.handle;

    tokio::spawn(async move {
        runner.run().await;
    });

    let client = Client::new(handle);
    let _ = api_hash;
    Ok(client)
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
    keyword_filters: &KeywordFilterCache,
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
            keyword_filters,
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
    keyword_filters: &KeywordFilterCache,
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
        let next = iter.next().await;
        match next {
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
                    keyword_filters,
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
    keyword_filters: &KeywordFilterCache,
) -> Option<ScrapedTelegramStream> {
    use grammers_client::media::Media;

    let (file_name, size, mime_type): (String, i64, Option<String>) = match message.media()? {
        Media::Document(doc) => {
            let is_video = document_is_video(&doc);
            let mut name = doc.name().unwrap_or("").to_string();
            if name.is_empty() {
                name = if is_video {
                    format!("video_{}.mp4", message.id())
                } else {
                    format!("file_{}", message.id())
                };
            }
            let size = doc.size().unwrap_or(0) as i64;
            let mime = doc.mime_type().map(str::to_string);
            if !is_video {
                let lower = name.to_lowercase();
                let mime_is_video = mime.as_deref().is_some_and(|m| m.starts_with("video/"));
                if !mime_is_video && !VIDEO_EXTENSIONS.iter().any(|ext| lower.ends_with(ext)) {
                    return None;
                }
            }
            (name, size, mime)
        }
        _ => return None,
    };

    // Minimum file size check
    if size > 0 && (size as u64) < min_size {
        return None;
    }

    // Adult content filter
    if keyword_filters.matches_blocked_keyword(&file_name) {
        return None;
    }

    // Parse title with PTT
    let parsed = parser::parse_title(&file_name);
    let caption_imdb_id = extract_caption_imdb(message.text());

    // Title similarity check (80% threshold) — skipped for feed/background scrapes.
    if !meta.title.is_empty() {
        let ratio =
            parser::similarity_ratio(parsed.title.as_deref().unwrap_or(&file_name), &meta.title);
        if ratio < 80 {
            return None;
        }
    }

    // For series: verify season/episode match
    if media_type == "series"
        && let (Some(s), Some(e)) = (season, episode)
    {
        let matches_season = parsed.seasons.contains(&s);
        let matches_ep = parsed.episodes.contains(&e);
        if !matches_season || !matches_ep {
            return None;
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
        caption_imdb_id,
    })
}

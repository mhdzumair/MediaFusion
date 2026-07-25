//! Request-time Telegram MTProto live scraper using `grammers-client`.
//!
//! Resolves each configured channel, iterates recent messages, extracts
//! documents that look like video files, parses them with PTT and filters
//! by title similarity before returning [`ScrapedTelegramStream`]s.

use grammers_client::Client;
use regex::Regex;
use std::collections::HashMap;
use std::sync::OnceLock;

use crate::{
    db::types::UserId,
    parser,
    scrapers::{ScrapedTelegramStream, SearchMeta},
    services::telegram_peer,
    state::{AppState, KeywordFilterCache},
    util::telegram_channel_id::{self, ChannelRef},
};

const VIDEO_EXTENSIONS: &[&str] = &[
    ".mkv", ".mp4", ".avi", ".webm", ".mov", ".flv", ".wmv", ".m4v",
];

/// Default messages fetched per channel when the user does not specify a limit.
pub const DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT: i32 = 25;

/// Parse a user-provided scrape depth.
///
/// - empty → default (`25`)
/// - `all` → no limit (`None`)
/// - positive integer → that many messages
pub fn parse_scrape_message_limit(input: &str) -> Result<Option<i32>, &'static str> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Ok(Some(DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT));
    }
    if trimmed.eq_ignore_ascii_case("all") {
        return Ok(None);
    }
    let value: i32 = trimmed.parse().map_err(|_| "invalid")?;
    if value <= 0 {
        return Err("invalid");
    }
    Ok(Some(value))
}

pub fn format_scrape_message_limit(limit: Option<i32>) -> String {
    match limit {
        None => "all messages".to_string(),
        Some(n) => format!("{n} messages"),
    }
}

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

// ─── Scrape entry point ───────────────────────────────────────────────────────

/// Scrape the given channels and return matching streams.
#[allow(clippy::too_many_arguments)]
pub async fn scrape(
    client: &Client,
    channels: &[String],
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    message_limit: Option<i32>,
    min_size: u64,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedTelegramStream> {
    let needs_dialog_lookup = channels.iter().any(|channel| {
        matches!(
            telegram_channel_id::parse_channel_ref(channel),
            Some(ChannelRef::DialogId(_))
        )
    });
    let dialog_peers = if needs_dialog_lookup {
        telegram_peer::load_dialog_peer_map(client).await
    } else {
        HashMap::new()
    };

    let mut results = Vec::new();
    for channel in channels {
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
            &dialog_peers,
        )
        .await;
        results.extend(channel_results);
    }
    results
}

/// Scrape configured channels for a user using their stored MTProto session.
pub async fn scrape_for_user(
    state: &AppState,
    user_id: UserId,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedTelegramStream> {
    if !state.telegram_clients.api_configured() {
        return vec![];
    }

    let channels = crate::db::telegram_channels::user_scraping_channels(&state.pool, user_id).await;
    if channels.is_empty() {
        return vec![];
    }

    let Some(client) = state
        .telegram_clients
        .get_client(&state.pool, user_id)
        .await
    else {
        tracing::debug!("telegram: no client for user {}", user_id.0);
        return vec![];
    };

    let keyword_filters = state
        .keyword_filters
        .read()
        .map(|g| g.clone())
        .unwrap_or_default();

    scrape(
        &client,
        &channels,
        meta,
        media_type,
        season,
        episode,
        Some(DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT),
        state.config.min_scraping_video_size,
        &keyword_filters,
    )
    .await
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
    message_limit: Option<i32>,
    min_size: u64,
    keyword_filters: &KeywordFilterCache,
    dialog_peers: &HashMap<i64, grammers_session::types::PeerRef>,
) -> Vec<ScrapedTelegramStream> {
    let Some(channel_ref) = telegram_channel_id::parse_channel_ref(channel) else {
        tracing::debug!("telegram: invalid channel identifier {channel}");
        return vec![];
    };

    let (peer, peer_ref) =
        match telegram_peer::resolve_channel_ref(client, channel_ref, dialog_peers).await {
            Some(found) => found,
            None => {
                tracing::debug!("telegram: could not resolve channel {channel}");
                return vec![];
            }
        };

    // Extract chat metadata for embedding into results
    let Some(chat_id) = peer.id().bot_api_dialog_id() else {
        tracing::debug!("telegram: peer has no bot API dialog id for {channel}");
        return vec![];
    };
    let chat_username: Option<String> = match &peer {
        grammers_client::peer::Peer::Channel(c) => c.username().map(str::to_string),
        grammers_client::peer::Peer::Group(g) => g.username().map(str::to_string),
        grammers_client::peer::Peer::User(u) => u.username().map(str::to_string),
    };

    // Iterate messages
    let mut iter = client.iter_messages(peer_ref);
    if let Some(limit) = message_limit {
        iter = iter.limit(limit as usize);
    }

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
                tracing::warn!("telegram: iter_messages {channel}: {e}");
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

    let (file_name, size, mime_type, document_id, file_unique_id): (
        String,
        i64,
        Option<String>,
        Option<i64>,
        Option<String>,
    ) = match message.media()? {
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
            let document_id = Some(doc.id());
            let file_unique_id = Some(doc.id().to_string());
            if !is_video {
                let lower = name.to_lowercase();
                let mime_is_video = mime.as_deref().is_some_and(|m| m.starts_with("video/"));
                if !mime_is_video && !VIDEO_EXTENSIONS.iter().any(|ext| lower.ends_with(ext)) {
                    return None;
                }
            }
            (name, size, mime, document_id, file_unique_id)
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
        document_id,
        file_unique_id,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_scrape_message_limit_defaults_and_all() {
        assert_eq!(
            parse_scrape_message_limit("").unwrap(),
            Some(DEFAULT_TELEGRAM_SCRAPE_MESSAGE_LIMIT)
        );
        assert_eq!(parse_scrape_message_limit("all").unwrap(), None);
        assert_eq!(parse_scrape_message_limit("50").unwrap(), Some(50));
        assert!(parse_scrape_message_limit("0").is_err());
    }
}

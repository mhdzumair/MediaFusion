//! Content-type detection from Telegram messages.

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{Value, json};

use super::model::{ContentType, Message};

static MAGNET_RE: OnceLock<Regex> = OnceLock::new();
static NZB_RE: OnceLock<Regex> = OnceLock::new();
static TORRENT_URL_RE: OnceLock<Regex> = OnceLock::new();
static YOUTUBE_RE: OnceLock<Regex> = OnceLock::new();
static ACESTREAM_RE: OnceLock<Regex> = OnceLock::new();
static HTTP_RE: OnceLock<Regex> = OnceLock::new();

const VIDEO_MIMES: &[&str] = &[
    "video/mp4",
    "video/x-matroska",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-flv",
    "video/mpeg",
    "video/3gpp",
    "video/x-ms-wmv",
];

const VIDEO_EXTENSIONS: &[&str] = &[".mkv", ".mp4", ".avi", ".webm", ".mov"];

fn magnet_re() -> &'static Regex {
    MAGNET_RE.get_or_init(|| Regex::new(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}[^\s]*").unwrap())
}

fn nzb_re() -> &'static Regex {
    NZB_RE.get_or_init(|| Regex::new(r"https?://[^\s]+\.nzb(?:\?[^\s]*)?").unwrap())
}

fn torrent_url_re() -> &'static Regex {
    TORRENT_URL_RE.get_or_init(|| {
        Regex::new(
            r"https?://[^\s]*(?:\.torrent(?:\?[^\s]*)?|/torrent(?:\?[^\s]+|/[^\s]+)|/torrents/[^\s]+)",
        )
        .unwrap()
    })
}

fn youtube_re() -> &'static Regex {
    YOUTUBE_RE.get_or_init(|| {
        Regex::new(
            r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
        )
        .unwrap()
    })
}

fn acestream_re() -> &'static Regex {
    ACESTREAM_RE.get_or_init(|| Regex::new(r"(?:acestream://)?([a-fA-F0-9]{40})").unwrap())
}

fn http_re() -> &'static Regex {
    HTTP_RE.get_or_init(|| Regex::new(r"https?://[^\s]+").unwrap())
}

fn extract_entity_urls(message: &Message) -> Vec<String> {
    let mut urls = Vec::new();
    let entities = message
        .entities
        .as_ref()
        .or(message.caption_entities.as_ref());
    let text = message.text.as_deref().or(message.caption.as_deref());
    let Some(text) = text else {
        return urls;
    };
    if let Some(entities) = entities {
        for entity in entities {
            if entity.r#type == "text_link" {
                if let Some(url) = &entity.url {
                    urls.push(url.clone());
                }
            } else if entity.r#type == "url" {
                let start = entity.offset as usize;
                let end = start + entity.length as usize;
                if end <= text.len() {
                    urls.push(text[start..end].to_string());
                }
            }
        }
    }
    urls
}

pub fn detect_content_type(message: &Message) -> Option<(ContentType, Value)> {
    let text = message
        .text
        .as_deref()
        .or(message.caption.as_deref())
        .unwrap_or("");
    let entity_urls = extract_entity_urls(message);

    if let Some(video) = &message.video {
        return Some((
            ContentType::Video,
            json!({
                "file_id": video.file_id,
                "file_unique_id": video.file_unique_id,
                "file_name": video.file_name,
                "file_size": video.file_size,
                "mime_type": video.mime_type,
            }),
        ));
    }

    if let Some(doc) = &message.document {
        let mime = doc.mime_type.as_deref().unwrap_or("");
        let fname = doc.file_name.as_deref().unwrap_or("");
        if mime == "application/x-bittorrent" || fname.ends_with(".torrent") {
            return Some((
                ContentType::TorrentFile,
                json!({
                    "file_id": doc.file_id,
                    "file_name": fname,
                    "file_size": doc.file_size,
                }),
            ));
        }
        if VIDEO_MIMES.contains(&mime)
            || VIDEO_EXTENSIONS
                .iter()
                .any(|ext| fname.to_ascii_lowercase().ends_with(ext))
        {
            return Some((
                ContentType::Video,
                json!({
                    "file_id": doc.file_id,
                    "file_unique_id": doc.file_unique_id,
                    "file_name": fname,
                    "file_size": doc.file_size,
                    "mime_type": mime,
                }),
            ));
        }
    }

    let mut candidates: Vec<String> = vec![text.to_string()];
    candidates.extend(entity_urls);

    for candidate in candidates {
        if candidate.is_empty() {
            continue;
        }
        if let Some(m) = magnet_re().find(&candidate) {
            return Some((ContentType::Magnet, json!(m.as_str())));
        }
        if let Some(m) = nzb_re().find(&candidate) {
            return Some((ContentType::Nzb, json!(m.as_str())));
        }
        if let Some(m) = torrent_url_re().find(&candidate) {
            return Some((ContentType::TorrentUrl, json!(m.as_str())));
        }
        if let Some(caps) = youtube_re().captures(&candidate) {
            let url = caps.get(0).map(|m| m.as_str()).unwrap_or("");
            let video_id = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            return Some((
                ContentType::Youtube,
                json!({ "url": url, "video_id": video_id }),
            ));
        }
        if let Some(caps) = acestream_re().captures(&candidate)
            && !candidate.to_ascii_lowercase().contains("magnet:")
        {
            let id = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            return Some((ContentType::Acestream, json!(id)));
        }
        if let Some(m) = http_re().find(&candidate) {
            let url = m.as_str();
            if magnet_re().is_match(url)
                || nzb_re().is_match(url)
                || torrent_url_re().is_match(url)
                || youtube_re().is_match(url)
            {
                continue;
            }
            return Some((ContentType::Http, json!(url)));
        }
    }

    None
}

pub fn content_type_label(ct: ContentType) -> &'static str {
    match ct {
        ContentType::Magnet => "🧲 Magnet Link",
        ContentType::TorrentFile => "📦 Torrent File",
        ContentType::TorrentUrl => "🔗📦 Torrent URL",
        ContentType::Nzb => "📰 NZB URL",
        ContentType::Youtube => "▶️ YouTube",
        ContentType::Http => "🔗 HTTP Stream",
        ContentType::Acestream => "📡 AceStream",
        ContentType::Video => "🎬 Video",
    }
}

pub fn content_preview(ct: ContentType, raw: &Value) -> String {
    match ct {
        ContentType::Magnet | ContentType::Nzb | ContentType::TorrentUrl | ContentType::Http => {
            raw.as_str().unwrap_or("…").chars().take(80).collect()
        }
        ContentType::Youtube => raw
            .get("url")
            .and_then(|v| v.as_str())
            .unwrap_or("YouTube")
            .to_string(),
        ContentType::Acestream => raw.as_str().unwrap_or("…").chars().take(40).collect(),
        ContentType::TorrentFile | ContentType::Video => raw
            .get("file_name")
            .and_then(|v| v.as_str())
            .unwrap_or("file")
            .chars()
            .take(60)
            .collect(),
    }
}

/// Normalize @channel / t.me/... to @username form.
pub fn normalize_channel_identifier(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    if s.starts_with('@') {
        let name = s.trim_start_matches('@').trim();
        if name.is_empty() {
            return None;
        }
        return Some(format!("@{name}"));
    }
    if s.contains("t.me/") {
        let path = s.split("t.me/").nth(1)?.split(['/', '?', '#']).next()?;
        if path.is_empty() || path.starts_with('+') {
            return None;
        }
        return Some(format!("@{path}"));
    }
    if s.starts_with("https://") || s.starts_with("http://") {
        return None;
    }
    Some(format!("@{s}"))
}

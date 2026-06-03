use std::sync::OnceLock;

use reqwest::Client;
use serde_json::Value;

use crate::{
    parser,
    scrapers::{ScrapedStream, SearchMeta},
};

pub async fn scrape(
    client: &Client,
    base_url: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let imdb_id = match &meta.imdb_id {
        Some(id) => id.clone(),
        None => {
            tracing::debug!(
                "torrentio: skipping — no imdb_id for media {}",
                meta.media_id
            );
            return vec![];
        }
    };

    let url = match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => {
            format!("{base_url}/stream/series/{imdb_id}:{s}:{e}.json")
        }
        _ => format!("{base_url}/stream/movie/{imdb_id}.json"),
    };

    let resp = match client
        .get(&url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "torrentio request failed url={url}: {e}"
            );
            return vec![];
        }
    };

    let json: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("torrentio response parse failed: {e}");
            return vec![];
        }
    };

    let streams = match json.get("streams").and_then(|v| v.as_array()) {
        Some(arr) => arr.clone(),
        None => return vec![],
    };

    streams.iter().filter_map(parse_stream).collect()
}

fn is_error_placeholder(url: &str) -> bool {
    let u = url.to_lowercase();
    u.contains("failed_access") || (u.contains("/videos/") && u.ends_with(".mp4"))
}

fn parse_stream(stream: &Value) -> Option<ScrapedStream> {
    // Check all URL fields for error placeholders
    for key in &["url", "externalUrl", "videoUrl"] {
        if let Some(url) = stream.get(key).and_then(|v| v.as_str()) {
            if is_error_placeholder(url) {
                return None;
            }
        }
    }
    if let Some(sources) = stream.get("sources").and_then(|v| v.as_array()) {
        for src in sources {
            if let Some(url) = src.as_str() {
                if is_error_placeholder(url) {
                    return None;
                }
            }
        }
    }

    let title_raw = stream.get("title").and_then(|v| v.as_str())?;
    if title_raw.trim().is_empty() {
        return None;
    }
    let torrent_name = title_raw.lines().next()?.to_string();

    let name_raw = stream.get("name").and_then(|v| v.as_str())?;
    if name_raw.trim().is_empty() {
        return None;
    }
    let source = name_raw
        .lines()
        .next()?
        .split_whitespace()
        .last()?
        .to_string();

    // Resolve info_hash: direct field first, then URL candidates
    let info_hash = if let Some(h) = stream.get("infoHash").and_then(|v| v.as_str()) {
        h.to_lowercase()
    } else {
        let mut found: Option<String> = None;
        for key in &["url", "externalUrl", "videoUrl"] {
            if let Some(url) = stream.get(key).and_then(|v| v.as_str()) {
                if is_error_placeholder(url) {
                    return None;
                }
                if let Some(h) = parser::extract_info_hash(url) {
                    found = Some(h);
                    break;
                }
            }
        }
        if found.is_none() {
            if let Some(sources) = stream.get("sources").and_then(|v| v.as_array()) {
                for src in sources {
                    if let Some(url) = src.as_str() {
                        if let Some(h) = parser::extract_info_hash(url) {
                            found = Some(h);
                            break;
                        }
                    }
                }
            }
        }
        found?
    };

    let is_cached = name_raw.contains('+');
    let size = parse_size(title_raw);
    let seeders = parse_seeders(title_raw);
    let parsed = parser::parse_title(&torrent_name);

    Some(ScrapedStream {
        info_hash,
        name: torrent_name,
        source,
        seeders,
        size,
        parsed,
        files: vec![],
        is_cached,
        torrent_type: crate::db::TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
    })
}

fn parse_size(desc: &str) -> Option<i64> {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"(?i)(\d+(?:\.\d+)?)\s*(GB|MB|TB|KB)").unwrap());
    let caps = re.captures(desc)?;
    let amount: f64 = caps.get(1)?.as_str().parse().ok()?;
    let unit = caps.get(2)?.as_str().to_uppercase();
    Some(match unit.as_str() {
        "TB" => (amount * 1_099_511_627_776.0) as i64,
        "GB" => (amount * 1_073_741_824.0) as i64,
        "MB" => (amount * 1_048_576.0) as i64,
        "KB" => (amount * 1024.0) as i64,
        _ => return None,
    })
}

fn parse_seeders(desc: &str) -> Option<i32> {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"(?i)👤\s*(\d+)|Seeds?[:\s]+(\d+)").unwrap());
    let caps = re.captures(desc)?;
    let n = caps.get(1).or_else(|| caps.get(2))?;
    n.as_str().parse().ok()
}

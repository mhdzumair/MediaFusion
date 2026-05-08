/// Peer MediaFusion instance scraper.
///
/// Calls the Stremio stream endpoint on a peer MediaFusion instance using the
/// public "D-" (empty user data) prefix so no auth is required.
///
/// Stream description format (from Python):
///   line 0: "📂 <torrent_name> ┈➤ <extra>"  or just "<torrent_name>"
///   name field first token: source (e.g. "YTS", "RARBG")
///   "⚡️" in name → debrid-cached
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
            tracing::debug!("mediafusion peer: skipping — no imdb_id for {}", meta.media_id);
            return vec![];
        }
    };

    // Use "D-" prefix (empty encrypted user data = public scope).
    let url = match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => {
            format!("{base_url}/D-/stream/series/{imdb_id}:{s}:{e}.json")
        }
        _ => format!("{base_url}/D-/stream/movie/{imdb_id}.json"),
    };

    let resp = match client
        .get(&url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::debug!("mediafusion peer request failed url={url}: {e}");
            return vec![];
        }
    };

    let json: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("mediafusion peer parse failed: {e}");
            return vec![];
        }
    };

    let streams = match json.get("streams").and_then(|v| v.as_array()) {
        Some(arr) => arr.clone(),
        None => return vec![],
    };

    streams.iter().filter_map(parse_stream).collect()
}

fn parse_stream(stream: &Value) -> Option<ScrapedStream> {
    let description = stream.get("description").and_then(|v| v.as_str())?;
    if description.trim().is_empty() {
        return None;
    }

    let name_raw = stream.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let source = name_raw
        .split_whitespace()
        .next()
        .unwrap_or("MediaFusion")
        .to_string();
    let is_cached = name_raw.contains('⚡');

    // Extract torrent name: first line, strip "📂 " prefix, take part before " ┈➤ "
    let first_line = description.lines().next().unwrap_or("").trim();
    let stripped = first_line.trim_start_matches("📂 ").trim();
    let torrent_name = stripped
        .split(" ┈➤ ")
        .next()
        .unwrap_or(stripped)
        .trim()
        .to_string();

    if torrent_name.is_empty() {
        return None;
    }

    // Resolve info_hash from infoHash field or streaming_provider URL
    let info_hash = if let Some(h) = stream.get("infoHash").and_then(|v| v.as_str()) {
        h.to_lowercase()
    } else {
        let url = stream.get("url").and_then(|v| v.as_str()).unwrap_or("");
        if url.contains("/streaming_provider/") {
            // URL shape: /streaming_provider/{info_hash}/...
            url.split('/').nth(2).map(|s| s.to_lowercase())?
        } else {
            return None;
        }
    };

    if info_hash.len() != 40 || !info_hash.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }

    let size = parse_size(description);
    let seeders = parse_seeders(description);
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
    })
}

fn parse_size(desc: &str) -> Option<i64> {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        regex::Regex::new(r"(?i)(\d+(?:\.\d+)?)\s*(GB|MB|TB|KB)").unwrap()
    });
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
    let re = RE.get_or_init(|| {
        regex::Regex::new(r"(?i)👤\s*(\d+)|Seeds?[:\s]+(\d+)").unwrap()
    });
    let caps = re.captures(desc)?;
    let n = caps.get(1).or_else(|| caps.get(2))?;
    n.as_str().parse().ok()
}

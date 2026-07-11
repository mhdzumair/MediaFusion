/// Peer MediaFusion instance scraper.
///
/// Calls the Kodi stream endpoint on a peer MediaFusion instance (no auth required).
/// Parses the rich `{ stream, metadata }` format returned by the kodi endpoint.
use reqwest::Client;
use serde_json::Value;

use crate::{
    parser::ParsedTitle,
    scrapers::{ScrapedStream, SearchMeta},
};

pub async fn scrape(
    client: &Client,
    base_url: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    secret_str: Option<&str>,
) -> Vec<ScrapedStream> {
    let imdb_id = match &meta.imdb_id {
        Some(id) => id.clone(),
        None => {
            tracing::debug!(
                "mediafusion peer: skipping — no imdb_id for {}",
                meta.media_id
            );
            return vec![];
        }
    };

    // Build URL: with secret_str uses authenticated kodi endpoint, otherwise public.
    let url = if let Some(ss) = secret_str.filter(|s| !s.is_empty()) {
        match (media_type, season, episode) {
            ("series", Some(s), Some(e)) => {
                format!("{base_url}/{ss}/kodi/stream/series/{imdb_id}:{s}:{e}.json?page_size=100")
            }
            _ => format!("{base_url}/{ss}/kodi/stream/movie/{imdb_id}.json?page_size=100"),
        }
    } else {
        match (media_type, season, episode) {
            ("series", Some(s), Some(e)) => {
                format!("{base_url}/kodi/stream/series/{imdb_id}:{s}:{e}.json?page_size=100")
            }
            _ => format!("{base_url}/kodi/stream/movie/{imdb_id}.json?page_size=100"),
        }
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
                "mediafusion peer request failed url={url}: {e}"
            );
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

    streams.iter().filter_map(parse_rich_stream).collect()
}

fn parse_rich_stream(item: &Value) -> Option<ScrapedStream> {
    let meta = item.get("metadata")?;
    let info_hash = meta.get("info_hash")?.as_str()?;
    if info_hash.len() != 40 || !info_hash.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let name = meta
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if name.is_empty() {
        return None;
    }
    let source = meta
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or("MediaFusion")
        .to_string();
    let quality = meta
        .get("quality")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let resolution = meta
        .get("resolution")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let codec = meta
        .get("codec")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let size = meta.get("size").and_then(|v| v.as_i64());
    let seeders = meta
        .get("seeders")
        .and_then(|v| v.as_i64())
        .map(|n| n as i32);
    let is_cached = meta
        .get("cached")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // Build ParsedTitle from the already-parsed fields (no re-parsing needed)
    let parsed = ParsedTitle {
        quality,
        resolution,
        codec,
        title: Some(name.clone()),
        episode_title: None,
        year: None,
        audio: vec![],
        channels: vec![],
        hdr: vec![],
        languages: vec![],
        seasons: vec![],
        episodes: vec![],
        is_proper: false,
        is_repack: false,
        is_extended: false,
        is_complete: false,
        is_dubbed: false,
        is_subbed: false,
        is_remastered: false,
        is_upscaled: false,
        release_group: None,
    };

    Some(ScrapedStream {
        info_hash: info_hash.to_lowercase(),
        name,
        source,
        seeders,
        size,
        parsed,
        files: vec![],
        is_cached,
        torrent_type: crate::db::TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        uploader: None,
    })
}

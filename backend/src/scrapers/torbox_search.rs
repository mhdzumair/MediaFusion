/// TorBox Search API scraper.
///
/// Mirrors Python `scrapers/torbox_search.py`.
/// Uses Bearer token from user_data.streaming_providers (service == "torbox").
///
/// Searches IMDb ID first, then title query; deduplicates by info_hash.
use reqwest::Client;

use crate::{
    models::user_data::UserData,
    parser,
    scrapers::{prowlarr::build_series_files, ScrapedStream, ScrapedUsenetStream, SearchMeta},
};

const BASE_URL: &str = "https://search-api.torbox.app";

pub async fn scrape(
    client: &Client,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let token = match torbox_token(user_data) {
        Some(t) => t,
        None => return vec![],
    };

    let headers = {
        let mut h = reqwest::header::HeaderMap::new();
        if let Ok(v) = reqwest::header::HeaderValue::from_str(&format!("Bearer {token}")) {
            h.insert(reqwest::header::AUTHORIZATION, v);
        }
        h
    };

    let mut results: Vec<ScrapedStream> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    // 1. IMDb ID search (most accurate)
    if let Some(ref imdb_id) = meta.imdb_id {
        let by_id = search_by_imdb(client, &headers, imdb_id, media_type, season, episode).await;
        for s in by_id {
            if seen.insert(s.info_hash.clone()) {
                results.push(s);
            }
        }
    }

    // 2. Title query search (additional results)
    let query = build_query(&meta.title, media_type, season, episode);
    let by_title =
        search_by_query(client, &headers, &query, media_type, season, episode, meta).await;
    for s in by_title {
        if seen.insert(s.info_hash.clone()) {
            results.push(s);
        }
    }

    results
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn torbox_token(user_data: &UserData) -> Option<String> {
    user_data
        .streaming_providers
        .iter()
        .find(|sp| sp.service == "torbox")
        .and_then(|sp| sp.token.clone())
}

fn build_query(title: &str, media_type: &str, season: Option<i32>, episode: Option<i32>) -> String {
    if media_type == "series" {
        match (season, episode) {
            (Some(s), Some(e)) => format!("{title} S{s:02}E{e:02}"),
            (Some(s), None) => format!("{title} S{s:02}"),
            _ => title.to_string(),
        }
    } else {
        title.to_string()
    }
}

async fn search_by_imdb(
    client: &Client,
    headers: &reqwest::header::HeaderMap,
    imdb_id: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let url = format!("{BASE_URL}/torrents/{imdb_id}");
    let mut params = vec![
        ("metadata", "true"),
        ("check_cache", "false"),
        ("check_owned", "false"),
    ];
    let season_str;
    let episode_str;
    if media_type == "series" {
        if let Some(s) = season {
            season_str = s.to_string();
            params.push(("season", &season_str));
            if let Some(e) = episode {
                episode_str = e.to_string();
                params.push(("episode", &episode_str));
            }
        }
    }

    let resp = client
        .get(&url)
        .headers(headers.clone())
        .query(&params)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await;

    match resp {
        Ok(r) if r.status().as_u16() == 404 || r.status().as_u16() == 418 => vec![],
        Ok(r) if r.status().is_success() => {
            let json: serde_json::Value = r.json().await.unwrap_or_default();
            parse_torrents_json(&json, media_type, season, episode, None)
        }
        Ok(r) => {
            tracing::debug!("torbox_search imdb {imdb_id}: HTTP {}", r.status());
            vec![]
        }
        Err(e) => {
            tracing::debug!("torbox_search imdb {imdb_id}: {e}");
            vec![]
        }
    }
}

async fn search_by_query(
    client: &Client,
    headers: &reqwest::header::HeaderMap,
    query: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    meta: &SearchMeta,
) -> Vec<ScrapedStream> {
    let encoded = urlencoding::encode(query);
    let url = format!("{BASE_URL}/torrents/search/{encoded}");
    let params = [
        ("metadata", "true"),
        ("check_cache", "false"),
        ("check_owned", "false"),
    ];

    let resp = client
        .get(&url)
        .headers(headers.clone())
        .query(&params)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await;

    match resp {
        Ok(r) if r.status().as_u16() == 404 => vec![],
        Ok(r) if r.status().is_success() => {
            let json: serde_json::Value = r.json().await.unwrap_or_default();
            parse_torrents_json(&json, media_type, season, episode, Some(meta))
        }
        Ok(r) => {
            tracing::debug!("torbox_search query {query}: HTTP {}", r.status());
            vec![]
        }
        Err(e) => {
            tracing::debug!("torbox_search query {query}: {e}");
            vec![]
        }
    }
}

fn parse_torrents_json(
    json: &serde_json::Value,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    meta: Option<&SearchMeta>,
) -> Vec<ScrapedStream> {
    let torrents = match json
        .get("data")
        .and_then(|d| d.get("torrents"))
        .and_then(|t| t.as_array())
    {
        Some(arr) => arr,
        None => return vec![],
    };

    let mut results = Vec::new();
    for torrent in torrents {
        let raw_title = torrent
            .get("raw_title")
            .or_else(|| torrent.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or_default();
        let info_hash = torrent
            .get("hash")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_lowercase();

        if raw_title.is_empty() || info_hash.len() != 40 {
            continue;
        }
        if parser::contains_adult_keywords(raw_title) {
            continue;
        }

        let parsed = parser::parse_title(raw_title);

        // Title similarity check for query-based results
        if let Some(m) = meta {
            let ratio =
                parser::similarity_ratio(parsed.title.as_deref().unwrap_or(raw_title), &m.title);
            if ratio < 80 {
                continue;
            }
        }

        let size = torrent.get("size").and_then(|v| {
            v.as_i64()
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        });
        let seeders = torrent
            .get("seeders")
            .and_then(|v| v.as_i64())
            .map(|s| s as i32);

        let files = if media_type == "series" {
            build_series_files(&parsed, season, episode)
        } else {
            vec![]
        };

        if media_type == "series" && files.is_empty() {
            continue;
        }

        results.push(ScrapedStream {
            info_hash,
            name: raw_title.to_string(),
            source: "TorBox Search".to_string(),
            seeders,
            size,
            parsed,
            files,
            is_cached: false,
            torrent_type: crate::db::TorrentType::Public,
            torrent_file: None,
            announce_list: vec![],
        });
    }

    results
}

// ─── Usenet scraper ───────────────────────────────────────────────────────────

pub async fn scrape_usenet(
    client: &Client,
    user_data: &UserData,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedUsenetStream> {
    let token = match torbox_token(user_data) {
        Some(t) => t,
        None => return vec![],
    };

    let mut headers = reqwest::header::HeaderMap::new();
    if let Ok(v) = reqwest::header::HeaderValue::from_str(&format!("Bearer {token}")) {
        headers.insert(reqwest::header::AUTHORIZATION, v);
    }

    let mut results: Vec<ScrapedUsenetStream> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    // 1. IMDb ID search — returns None on 429 (rate-limited); skip title query in that case
    if let Some(ref imdb_id) = meta.imdb_id {
        match search_usenet_by_imdb(client, &headers, imdb_id, media_type, season, episode).await {
            None => return results, // rate-limited; skip title query to avoid another 429
            Some(by_id) => {
                for s in by_id {
                    if seen.insert(s.nzb_guid.clone()) {
                        results.push(s);
                    }
                }
            }
        }
    }

    // 2. Title query search
    let query = build_query(&meta.title, media_type, season, episode);
    let encoded = urlencoding::encode(&query);
    let url = format!("{BASE_URL}/usenet/search/{encoded}");
    let params = [
        ("metadata", "true"),
        ("check_cache", "false"),
        ("check_owned", "false"),
    ];
    let resp = client
        .get(&url)
        .headers(headers.clone())
        .query(&params)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await;
    match resp {
        Ok(r) if r.status().as_u16() == 404 => {}
        Ok(r) if r.status().is_success() => {
            let json: serde_json::Value = r.json().await.unwrap_or_default();
            for s in parse_usenet_json(&json, media_type, season, episode, Some(meta)) {
                if seen.insert(s.nzb_guid.clone()) {
                    results.push(s);
                }
            }
        }
        Ok(r) if r.status().as_u16() == 429 => {
            tracing::warn!("torbox_usenet query '{query}': rate-limited (429)");
        }
        Ok(r) => tracing::debug!("torbox_usenet query {query}: HTTP {}", r.status()),
        Err(e) => tracing::debug!("torbox_usenet query {query}: {e}"),
    }

    results
}

/// Returns `None` when TorBox signals rate-limiting (429) so the caller can
/// short-circuit and skip the follow-up title query.
async fn search_usenet_by_imdb(
    client: &Client,
    headers: &reqwest::header::HeaderMap,
    imdb_id: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<Vec<ScrapedUsenetStream>> {
    let url = format!("{BASE_URL}/usenet/{imdb_id}");
    let mut params: Vec<(&str, String)> = vec![
        ("metadata", "true".into()),
        ("check_cache", "false".into()),
        ("check_owned", "false".into()),
    ];
    if media_type == "series" {
        if let Some(s) = season {
            params.push(("season", s.to_string()));
            if let Some(e) = episode {
                params.push(("episode", e.to_string()));
            }
        }
    }

    let resp = client
        .get(&url)
        .headers(headers.clone())
        .query(&params)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await;

    match resp {
        Ok(r) if r.status().as_u16() == 404 || r.status().as_u16() == 418 => Some(vec![]),
        Ok(r) if r.status().is_success() => {
            let json: serde_json::Value = r.json().await.unwrap_or_default();
            Some(parse_usenet_json(&json, media_type, season, episode, None))
        }
        Ok(r) if r.status().as_u16() == 429 => {
            tracing::warn!(
                "torbox_usenet imdb {imdb_id}: rate-limited (429) — skipping title query"
            );
            None
        }
        Ok(r) => {
            tracing::debug!("torbox_usenet imdb {imdb_id}: HTTP {}", r.status());
            Some(vec![])
        }
        Err(e) => {
            tracing::debug!("torbox_usenet imdb {imdb_id}: {e}");
            Some(vec![])
        }
    }
}

fn parse_usenet_json(
    json: &serde_json::Value,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    meta: Option<&SearchMeta>,
) -> Vec<ScrapedUsenetStream> {
    let nzbs = match json
        .get("data")
        .and_then(|d| d.get("nzbs"))
        .and_then(|n| n.as_array())
    {
        Some(arr) => arr,
        None => return vec![],
    };

    let mut results = Vec::new();
    for nzb in nzbs {
        let raw_title = nzb
            .get("raw_title")
            .or_else(|| nzb.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or_default();
        let nzb_guid = nzb
            .get("hash")
            .or_else(|| nzb.get("id"))
            .or_else(|| nzb.get("guid"))
            .and_then(|v| {
                v.as_str()
                    .map(str::to_string)
                    .or_else(|| v.as_i64().map(|n| n.to_string()))
            })
            .unwrap_or_default();
        let nzb_url = nzb
            .get("nzb")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if raw_title.is_empty() || nzb_guid.is_empty() {
            continue;
        }
        if parser::contains_adult_keywords(raw_title) {
            continue;
        }

        let parsed = parser::parse_title(raw_title);

        if let Some(m) = meta {
            let ratio =
                parser::similarity_ratio(parsed.title.as_deref().unwrap_or(raw_title), &m.title);
            if ratio < 80 {
                continue;
            }
        }

        let files = if media_type == "series" {
            build_series_files(&parsed, season, episode)
        } else {
            vec![]
        };
        if media_type == "series" && files.is_empty() {
            continue;
        }

        let size = nzb
            .get("size")
            .and_then(|v| {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .unwrap_or(0);

        results.push(ScrapedUsenetStream {
            nzb_guid,
            nzb_url,
            name: raw_title.to_string(),
            size,
            indexer: "TorBox Search".to_string(),
            source: "TorBox Search".to_string(),
            group_name: None,
            parsed,
            files,
            is_cached: false,
        });
    }

    results
}

// Simple URL encoder (avoids adding a new dep; reqwest already has percent_encoding)
mod urlencoding {
    pub fn encode(s: &str) -> String {
        s.bytes()
            .flat_map(|b| {
                if b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.' || b == b'~' {
                    vec![b as char]
                } else {
                    format!("%{b:02X}").chars().collect::<Vec<_>>()
                }
            })
            .collect()
    }
}

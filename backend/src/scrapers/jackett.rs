use reqwest::Client;
use serde::Deserialize;
use std::time::Duration;

use crate::{
    parser,
    scrapers::{
        prowlarr::build_series_files,
        torrent_metadata::{
            self, download_torrent_bytes, jackett_torrent_type, parse_torrent_bytes,
            resolve_download_url, should_persist_torrent_file, torrent_file_for_storage,
        },
        ScrapedStream, SearchMeta,
    },
};

pub(crate) const RESULT_PROCESS_CONCURRENCY: usize = 5;

// ─── Jackett JSON response shapes ─────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct JackettResponse {
    #[serde(rename = "Results", default)]
    results: Vec<JackettResult>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct JackettResult {
    #[serde(rename = "Title")]
    title: Option<String>,
    #[serde(rename = "InfoHash")]
    info_hash: Option<String>,
    #[serde(rename = "MagnetUri")]
    magnet_uri: Option<String>,
    #[serde(rename = "Link")]
    link: Option<String>,
    #[serde(rename = "Guid")]
    guid: Option<String>,
    #[serde(rename = "Tracker")]
    tracker: Option<String>,
    #[serde(rename = "TrackerType")]
    tracker_type: Option<String>,
    #[serde(rename = "Seeders")]
    seeders: Option<i32>,
    #[serde(rename = "Size")]
    size: Option<i64>,
    #[serde(rename = "CategoryDesc", default)]
    category_desc: Option<String>,
}

// ─── Public entry point ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub async fn scrape(
    client: &Client,
    base_url: &str,
    api_key: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    max_process: usize,
    max_process_time: std::time::Duration,
    query_timeout: std::time::Duration,
) -> Vec<ScrapedStream> {
    let imdb_id = meta.imdb_id.as_deref().unwrap_or("");
    let is_series = media_type == "series";

    let query = if !imdb_id.is_empty() {
        format!("{{IMDbId:{imdb_id}}}")
    } else {
        build_title_query(&meta.title, meta.year, media_type, season, episode)
    };

    let categories: &[&str] = if is_series {
        &[
            "5000", "5010", "5020", "5030", "5040", "5045", "5050", "5060", "5070",
        ]
    } else {
        &[
            "2000", "2010", "2020", "2030", "2040", "2045", "2050", "2060", "2070",
        ]
    };

    let mut params: Vec<(&str, String)> = vec![("apikey", api_key.to_string()), ("Query", query)];
    for cat in categories {
        params.push(("Category[]", cat.to_string()));
    }

    match tokio::time::timeout(max_process_time, async {
        let resp = match client
            .get(format!("{base_url}/api/v2.0/indexers/all/results"))
            .query(&params)
            .timeout(query_timeout)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::debug!("jackett request failed: {e}");
                return vec![];
            }
        };

        let body: JackettResponse = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("jackett response parse failed: {e}");
                return vec![];
            }
        };

        let items: Vec<JackettResult> = body.results.into_iter().take(max_process).collect();
        use futures::stream::{self, StreamExt};
        stream::iter(items)
            .map(|r| process_result(client, r, media_type, season, episode, query_timeout))
            .buffer_unordered(RESULT_PROCESS_CONCURRENCY)
            .filter_map(|result| async move { result })
            .collect()
            .await
    })
    .await
    {
        Ok(r) => r,
        Err(_) => {
            tracing::debug!("jackett: max_process_time exceeded");
            vec![]
        }
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn build_title_query(
    title: &str,
    year: Option<i32>,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> String {
    if media_type == "series" {
        match (season, episode) {
            (Some(s), Some(e)) => format!("{title} S{s:02}E{e:02}"),
            (Some(s), None) => format!("{title} S{s:02}"),
            _ => title.to_string(),
        }
    } else if let Some(y) = year {
        format!("{title} {y}")
    } else {
        title.to_string()
    }
}

async fn process_result(
    client: &Client,
    item: JackettResult,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    query_timeout: Duration,
) -> Option<ScrapedStream> {
    let title = item.title.as_deref()?.trim().to_string();
    if title.is_empty() {
        return None;
    }

    let torrent_type = jackett_torrent_type(item.tracker_type.as_deref());
    let download_pick = resolve_download_url(
        torrent_type,
        item.guid.as_deref(),
        item.magnet_uri.as_deref(),
        item.link.as_deref(),
    );

    let mut info_hash = item
        .info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40 && h.chars().all(|c| c.is_ascii_hexdigit()));
    let mut announce_list: Vec<String> = Vec::new();
    let mut torrent_file: Option<Vec<u8>> = None;
    let mut size = item.size;

    let needs_download = should_persist_torrent_file(torrent_type) || info_hash.is_none();

    if needs_download {
        if let Some(url) = download_pick.as_deref() {
            if url.starts_with("magnet:") {
                info_hash = info_hash.or_else(|| parser::extract_info_hash(url));
                announce_list = torrent_metadata::announce_list_from_magnet(url);
            } else if let Some(bytes) = download_torrent_bytes(client, url, query_timeout).await {
                if let Some(parsed) = parse_torrent_bytes(&bytes) {
                    info_hash = Some(parsed.info_hash);
                    announce_list = parsed.announce_list;
                    size = size.filter(|s| *s > 0).or(Some(parsed.total_size));
                    torrent_file = torrent_file_for_storage(torrent_type, Some(parsed.raw_bytes));
                }
            }
        }
    }

    if info_hash.is_none() {
        if let Some(m) = item.magnet_uri.as_deref() {
            info_hash = parser::extract_info_hash(m);
            if announce_list.is_empty() {
                announce_list = torrent_metadata::announce_list_from_magnet(m);
            }
        }
    }

    let info_hash = info_hash?;
    let source = item.tracker.unwrap_or_else(|| "Jackett".to_string());
    let parsed = parser::parse_title(&title);
    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };

    Some(ScrapedStream {
        info_hash,
        name: title,
        source,
        seeders: item.seeders,
        size,
        parsed,
        files,
        is_cached: false,
        torrent_type,
        torrent_file,
        announce_list,
    })
}

pub(crate) fn resolve_result_info_hash(item: &JackettResult) -> Option<String> {
    item.info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40)
        .or_else(|| {
            item.magnet_uri
                .as_deref()
                .and_then(parser::extract_info_hash)
        })
}

pub(crate) fn media_type_from_category_desc(desc: Option<&str>) -> &'static str {
    match desc {
        Some(d) => {
            let lower = d.to_lowercase();
            if lower.contains("tv") || lower.contains("series") || lower.contains("episode") {
                "series"
            } else {
                "movie"
            }
        }
        None => "movie",
    }
}

pub(crate) async fn process_feed_results(
    client: &Client,
    items: Vec<JackettResult>,
    query_timeout: Duration,
) -> Vec<(ScrapedStream, &'static str)> {
    use futures::stream::{self, StreamExt};
    stream::iter(items)
        .map(|item| {
            let media_type = media_type_from_category_desc(item.category_desc.as_deref());
            async move {
                let stream =
                    process_result(client, item, media_type, None, None, query_timeout).await?;
                Some((stream, media_type))
            }
        })
        .buffer_unordered(RESULT_PROCESS_CONCURRENCY)
        .filter_map(|result| async move { result })
        .collect()
        .await
}

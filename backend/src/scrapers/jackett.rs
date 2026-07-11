use std::collections::{HashMap, HashSet};
use std::time::Duration;

use quick_xml::Reader;
use quick_xml::events::Event;
use reqwest::Client;
use serde::Deserialize;

use crate::{
    parser,
    scrapers::{
        ScrapedStream, SearchMeta,
        prowlarr::build_series_files,
        torrent_info,
        torrent_metadata::{
            self, download_torrent_bytes, jackett_torrent_type, parse_torrent_bytes,
            resolve_download_url, should_persist_torrent_file, torrent_file_for_storage,
        },
    },
};

pub(crate) const RESULT_PROCESS_CONCURRENCY: usize = 5;

const MOVIE_CATEGORY_IDS: &[i64] = &[2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070];
const SERIES_CATEGORY_IDS: &[i64] = &[5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070];

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

#[derive(Debug, Clone)]
pub struct JackettIndexer {
    pub id: String,
    pub name: String,
    pub categories: Vec<i64>,
    pub supports_imdb_movie: bool,
    pub supports_imdb_tv: bool,
    pub supports_search: bool,
}

pub async fn list_healthy_indexers(
    client: &Client,
    base_url: &str,
    api_key: &str,
) -> Vec<JackettIndexer> {
    let url = format!(
        "{}/api/v2.0/indexers/!status:failing/results/torznab/api",
        base_url.trim_end_matches('/')
    );
    let resp = match client
        .get(url)
        .query(&[
            ("apikey", api_key),
            ("t", "indexers"),
            ("configured", "true"),
        ])
        .timeout(Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            tracing::debug!("jackett: indexer list HTTP {}", r.status());
            return vec![];
        }
        Err(e) => {
            tracing::debug!("jackett: indexer list failed: {e}");
            return vec![];
        }
    };

    let xml = resp.text().await.unwrap_or_default();
    parse_jackett_indexers(&xml)
}

#[allow(clippy::too_many_arguments)]
pub async fn scrape_indexer(
    client: &Client,
    base_url: &str,
    api_key: &str,
    idx: &JackettIndexer,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    max_process: usize,
    query_timeout: Duration,
    title_queries: &[String],
    deadline: tokio::time::Instant,
) -> Vec<ScrapedStream> {
    if tokio::time::Instant::now() >= deadline {
        return vec![];
    }

    let queries = build_queries(meta, media_type, season, episode, title_queries);
    if queries.is_empty() {
        return vec![];
    }

    let categories: Vec<i64> = if media_type == "series" {
        SERIES_CATEGORY_IDS.to_vec()
    } else {
        MOVIE_CATEGORY_IDS.to_vec()
    };

    let mut all_results: Vec<JackettResult> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    for query in queries {
        if tokio::time::Instant::now() >= deadline {
            break;
        }

        let mut params: Vec<(&str, String)> = vec![
            ("apikey", api_key.to_string()),
            ("Query", query),
            ("Tracker[]", idx.id.clone()),
        ];
        for cat in &categories {
            params.push(("Category[]", cat.to_string()));
        }

        let resp = match client
            .get(format!(
                "{}/api/v2.0/indexers/all/results",
                base_url.trim_end_matches('/')
            ))
            .query(&params)
            .timeout(query_timeout)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::debug!(
                    error_kind = crate::util::http::transport_error_kind(&e),
                    "jackett indexer {} request failed: {e}",
                    idx.name
                );
                continue;
            }
        };

        let body: JackettResponse = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("jackett indexer {} parse failed: {e}", idx.name);
                continue;
            }
        };

        for result in body.results {
            let dedupe_key = result
                .info_hash
                .clone()
                .or_else(|| result.guid.clone())
                .unwrap_or_default()
                .to_lowercase();
            if dedupe_key.is_empty() || seen.insert(dedupe_key) {
                all_results.push(result);
                if all_results.len() >= max_process {
                    break;
                }
            }
        }
        if all_results.len() >= max_process {
            break;
        }
    }

    let items: Vec<JackettResult> = all_results.into_iter().take(max_process).collect();
    use futures::stream::{self, StreamExt};
    stream::iter(items)
        .map(|r| process_result(client, r, media_type, season, episode, query_timeout))
        .buffer_unordered(RESULT_PROCESS_CONCURRENCY)
        .filter_map(|result| async move { result })
        .collect()
        .await
}

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
    title_queries: &[String],
) -> Vec<ScrapedStream> {
    let indexers = list_healthy_indexers(client, base_url, api_key).await;
    if indexers.is_empty() {
        return vec![];
    }

    let deadline = tokio::time::Instant::now() + max_process_time;
    let mut all = Vec::new();
    for idx in &indexers {
        if tokio::time::Instant::now() >= deadline {
            break;
        }
        let mut batch = scrape_indexer(
            client,
            base_url,
            api_key,
            idx,
            meta,
            media_type,
            season,
            episode,
            max_process,
            query_timeout,
            title_queries,
            deadline,
        )
        .await;
        all.append(&mut batch);
    }
    all
}

fn build_queries(
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    title_queries: &[String],
) -> Vec<String> {
    let imdb_id = meta.imdb_id.as_deref().unwrap_or("");
    let mut queries = Vec::new();
    if !imdb_id.is_empty() {
        queries.push(format!("{{IMDbId:{imdb_id}}}"));
    }
    if !title_queries.is_empty() {
        queries.extend(title_queries.iter().cloned());
    } else if imdb_id.is_empty() {
        queries.push(build_title_query(
            &meta.title,
            meta.year,
            media_type,
            season,
            episode,
        ));
    }
    queries
}

fn parse_jackett_indexers(xml: &str) -> Vec<JackettIndexer> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut buf = Vec::new();
    let mut indexers = Vec::new();
    let mut current_id: Option<String> = None;
    let mut current_name = String::new();
    let mut current_categories: Vec<i64> = Vec::new();
    let mut search_caps: HashSet<String> = HashSet::new();
    let mut movie_params: HashSet<String> = HashSet::new();
    let mut tv_params: HashSet<String> = HashSet::new();

    let flush = |indexers: &mut Vec<JackettIndexer>,
                 id: Option<String>,
                 name: String,
                 categories: Vec<i64>,
                 search_caps: &HashSet<String>,
                 movie_params: &HashSet<String>,
                 tv_params: &HashSet<String>| {
        let Some(id) = id else { return };
        if id.is_empty() {
            return;
        }
        indexers.push(JackettIndexer {
            id,
            name: if name.is_empty() {
                "unknown".into()
            } else {
                name
            },
            categories,
            supports_imdb_movie: search_caps.contains("movie-search")
                && movie_params
                    .iter()
                    .any(|p| p.eq_ignore_ascii_case("imdbid")),
            supports_imdb_tv: search_caps.contains("tv-search")
                && tv_params.iter().any(|p| p.eq_ignore_ascii_case("imdbid")),
            supports_search: search_caps.contains("search"),
        });
    };

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                let tag = xml_tag_name(e.name().as_ref());
                let attrs = xml_attrs(e);
                match tag.as_str() {
                    "indexer" => {
                        flush(
                            &mut indexers,
                            current_id.take(),
                            std::mem::take(&mut current_name),
                            std::mem::take(&mut current_categories),
                            &search_caps,
                            &movie_params,
                            &tv_params,
                        );
                        search_caps.clear();
                        movie_params.clear();
                        tv_params.clear();
                        current_id = attrs.get("id").cloned();
                    }
                    "category" => {
                        if let Some(id) = attrs.get("id").and_then(|s| s.parse().ok()) {
                            current_categories.push(id);
                        }
                    }
                    "subcat" => {
                        if let Some(id) = attrs.get("id").and_then(|s| s.parse().ok()) {
                            current_categories.push(id);
                        }
                    }
                    "search" | "tv-search" | "movie-search"
                        if attrs.get("available").map(String::as_str) == Some("yes") =>
                    {
                        search_caps.insert(tag.clone());
                        if let Some(params) = attrs.get("supportedParams") {
                            let set = match tag.as_str() {
                                "movie-search" => &mut movie_params,
                                "tv-search" => &mut tv_params,
                                _ => &mut movie_params,
                            };
                            for p in params.split(',') {
                                set.insert(p.trim().to_string());
                            }
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Text(ref e)) if current_id.is_some() && current_name.is_empty() => {
                if let Ok(text) = e.decode() {
                    let s = text.trim();
                    if !s.is_empty() {
                        current_name = s.to_string();
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(_) => break,
            _ => {}
        }
        buf.clear();
    }

    flush(
        &mut indexers,
        current_id.take(),
        current_name,
        current_categories,
        &search_caps,
        &movie_params,
        &tv_params,
    );

    indexers.sort_by(|a, b| a.name.cmp(&b.name));
    indexers
}

fn xml_tag_name(name: &[u8]) -> String {
    std::str::from_utf8(name).unwrap_or("").to_lowercase()
}

fn xml_attrs(e: &quick_xml::events::BytesStart) -> HashMap<String, String> {
    e.attributes()
        .filter_map(|a| a.ok())
        .map(|a| {
            (
                String::from_utf8_lossy(a.key.as_ref()).to_string(),
                String::from_utf8_lossy(&a.value).to_string(),
            )
        })
        .collect()
}

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

    if needs_download && let Some(url) = download_pick.as_deref() {
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
        } else {
            let indexer_name = item.tracker.as_deref().unwrap_or("Jackett");
            let page_info =
                torrent_info::get_torrent_info(client, url, indexer_name, query_timeout).await;
            if let Some(magnet) = page_info.magnet_url.as_deref() {
                info_hash = info_hash.or_else(|| parser::extract_info_hash(magnet));
                if announce_list.is_empty() {
                    announce_list = torrent_metadata::announce_list_from_magnet(magnet);
                }
            }
            if info_hash.is_none() {
                info_hash = page_info
                    .info_hash
                    .map(|h| h.to_lowercase())
                    .filter(|h| h.len() == 40);
            }
            if torrent_file.is_none()
                && let Some(dl) = page_info.download_url.as_deref()
                && let Some(bytes) = download_torrent_bytes(client, dl, query_timeout).await
                && let Some(parsed) = parse_torrent_bytes(&bytes)
            {
                info_hash = Some(parsed.info_hash);
                announce_list = parsed.announce_list;
                size = size.filter(|s| *s > 0).or(Some(parsed.total_size));
                torrent_file = torrent_file_for_storage(torrent_type, Some(parsed.raw_bytes));
            }
        }
    }

    if info_hash.is_none()
        && let Some(m) = item.magnet_uri.as_deref()
    {
        info_hash = parser::extract_info_hash(m);
        if announce_list.is_empty() {
            announce_list = torrent_metadata::announce_list_from_magnet(m);
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
        uploader: None,
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

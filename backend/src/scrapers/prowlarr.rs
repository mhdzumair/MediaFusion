use reqwest::Client;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::time::Duration;

use crate::{
    parser,
    scrapers::{
        ScrapedStream, SearchMeta, StreamFile, torrent_info,
        torrent_metadata::{
            self, download_torrent_bytes, parse_torrent_bytes, prowlarr_torrent_type,
            resolve_download_url, should_persist_torrent_file, torrent_file_for_storage,
        },
    },
};

pub(crate) const RESULT_PROCESS_CONCURRENCY: usize = 5;

fn format_request_error(e: &(dyn std::error::Error + Send + Sync)) -> String {
    let msg = e.to_string();
    if msg.contains("401 Unauthorized") {
        return "HTTP 401 Unauthorized — invalid or missing X-Api-Key (check PROWLARR_API_KEY or profile indexer API key)".into();
    }
    if msg.contains("timed out") {
        return "request timed out".into();
    }
    msg
}

// ─── Prowlarr response shapes ─────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct IndexerInfo {
    id: i64,
    #[serde(default)]
    enable: bool,
    #[serde(default)]
    name: String,
    #[serde(default)]
    priority: i64,
    #[serde(default)]
    privacy: String,
    #[serde(default)]
    capabilities: IndexerCaps,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct IndexerCaps {
    #[serde(default)]
    search_params: Vec<String>,
    #[serde(default)]
    tv_search_params: Vec<String>,
    #[serde(default)]
    movie_search_params: Vec<String>,
    #[serde(default)]
    categories: Vec<CategoryInfo>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct CategoryInfo {
    id: i64,
    #[serde(default)]
    sub_categories: Vec<SubCategoryInfo>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct SubCategoryInfo {
    id: i64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct IndexerStatus {
    indexer_id: i64,
    #[serde(default)]
    disabled_till: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SearchResult {
    #[serde(default)]
    info_hash: Option<String>,
    #[serde(default)]
    magnet_url: Option<String>,
    #[serde(default)]
    download_url: Option<String>,
    #[serde(default)]
    guid: Option<String>,
    #[serde(default)]
    indexer_id: Option<i64>,
    #[serde(default)]
    indexer_flags: Vec<String>,
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    indexer: Option<String>,
    #[serde(default)]
    seeders: Option<i32>,
    #[serde(default)]
    size: Option<i64>,
    #[serde(default)]
    categories: Vec<Value>,
}

// ─── Healthy indexer (local representation) ──────────────────────────────────

#[derive(Debug, Clone)]
pub struct ProwlarrIndexer {
    pub id: i64,
    pub name: String,
    pub priority: i64,
    pub is_public: bool,
    pub privacy: String,
    pub categories: Vec<i64>,
    pub supports_imdb_movie: bool,
    pub supports_imdb_tv: bool,
    pub supports_basic_search: bool,
}

#[derive(Debug, Clone)]
struct Indexer {
    id: i64,
    name: String,
    priority: i64,
    is_public: bool,
    privacy: String,
    categories: Vec<i64>,
    supports_imdb_movie: bool,
    supports_imdb_tv: bool,
    supports_basic_search: bool,
}

impl From<Indexer> for ProwlarrIndexer {
    fn from(value: Indexer) -> Self {
        Self {
            id: value.id,
            name: value.name,
            priority: value.priority,
            is_public: value.is_public,
            privacy: value.privacy,
            categories: value.categories,
            supports_imdb_movie: value.supports_imdb_movie,
            supports_imdb_tv: value.supports_imdb_tv,
            supports_basic_search: value.supports_basic_search,
        }
    }
}

// ─── Public entry points ──────────────────────────────────────────────────────

pub async fn list_healthy_indexers(
    client: &Client,
    base_url: &str,
    api_key: &str,
) -> Vec<ProwlarrIndexer> {
    match fetch_indexers(client, base_url, api_key).await {
        Ok(indexers) => indexers.into_iter().map(ProwlarrIndexer::from).collect(),
        Err(e) => {
            tracing::debug!(
                "prowlarr: failed to fetch indexers: {}",
                format_request_error(&*e)
            );
            vec![]
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub async fn scrape_indexer(
    client: &Client,
    base_url: &str,
    api_key: &str,
    idx: &ProwlarrIndexer,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    max_process: usize,
    query_timeout: Duration,
    title_queries: &[String],
    privacy_by_id: &HashMap<i64, String>,
    deadline: tokio::time::Instant,
) -> Vec<ScrapedStream> {
    let idx = Indexer {
        id: idx.id,
        name: idx.name.clone(),
        priority: idx.priority,
        is_public: idx.is_public,
        privacy: idx.privacy.clone(),
        categories: idx.categories.clone(),
        supports_imdb_movie: idx.supports_imdb_movie,
        supports_imdb_tv: idx.supports_imdb_tv,
        supports_basic_search: idx.supports_basic_search,
    };
    scrape_indexer_inner(
        client,
        base_url,
        api_key,
        &idx,
        meta,
        media_type,
        season,
        episode,
        max_process,
        query_timeout,
        title_queries,
        privacy_by_id,
        deadline,
    )
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
    let indexers = match fetch_indexers(client, base_url, api_key).await {
        Ok(v) if !v.is_empty() => v,
        Ok(_) => {
            tracing::debug!("prowlarr: no healthy indexers");
            return vec![];
        }
        Err(e) => {
            tracing::debug!(
                "prowlarr: failed to fetch indexers: {}",
                format_request_error(&*e)
            );
            return vec![];
        }
    };

    let privacy_by_id: HashMap<i64, String> =
        indexers.iter().map(|i| (i.id, i.privacy.clone())).collect();
    let deadline = tokio::time::Instant::now() + max_process_time;
    let mut results = Vec::new();

    for idx in &indexers {
        if tokio::time::Instant::now() >= deadline {
            break;
        }
        let mut batch = scrape_indexer_inner(
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
            &privacy_by_id,
            deadline,
        )
        .await;
        results.append(&mut batch);
    }

    results
}

#[allow(clippy::too_many_arguments)]
async fn scrape_indexer_inner(
    client: &Client,
    base_url: &str,
    api_key: &str,
    idx: &Indexer,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    max_process: usize,
    query_timeout: Duration,
    title_queries: &[String],
    privacy_by_id: &HashMap<i64, String>,
    deadline: tokio::time::Instant,
) -> Vec<ScrapedStream> {
    let imdb_id = meta.imdb_id.as_deref().unwrap_or("");
    let is_series = media_type == "series";
    let mut results: Vec<ScrapedStream> = Vec::new();
    let mut consecutive_failures: u32 = 0;

    if tokio::time::Instant::now() >= deadline {
        return results;
    }

    let (imdb_search_type, imdb_categories) = if is_series && idx.supports_imdb_tv {
        ("tvsearch", movie_tv_categories(idx, true))
    } else if !is_series && idx.supports_imdb_movie {
        ("movie", movie_tv_categories(idx, false))
    } else {
        ("", Vec::new())
    };

    let fallback_search_type = if is_series && idx.supports_imdb_tv {
        "tvsearch"
    } else if !is_series && idx.supports_imdb_movie {
        "movie"
    } else if idx.supports_imdb_movie || idx.supports_imdb_tv || idx.supports_basic_search {
        "search"
    } else {
        return results;
    };
    let fallback_categories = if imdb_categories.is_empty() {
        if fallback_search_type == "search" {
            idx.categories.clone()
        } else {
            movie_tv_categories(idx, is_series)
        }
    } else {
        imdb_categories.clone()
    };

    let mut queries: Vec<(String, &str, Vec<i64>)> = Vec::new();

    if !imdb_id.is_empty() && !imdb_search_type.is_empty() {
        queries.push((
            format!("{{IMDbId:{imdb_id}}}"),
            imdb_search_type,
            imdb_categories,
        ));
    }

    if !title_queries.is_empty()
        && (idx.supports_basic_search || idx.supports_imdb_movie || idx.supports_imdb_tv)
    {
        for title_query in title_queries {
            queries.push((title_query.clone(), "search", idx.categories.clone()));
        }
    } else if imdb_id.is_empty() && title_queries.is_empty() {
        queries.push((
            meta.title.clone(),
            fallback_search_type,
            fallback_categories,
        ));
    }

    for (query, search_type, categories) in queries {
        if tokio::time::Instant::now() >= deadline {
            tracing::debug!(
                "prowlarr: max_process_time exceeded for indexer {}",
                idx.name
            );
            break;
        }

        if consecutive_failures >= 3 {
            tracing::debug!("prowlarr: stopping indexer {} after 3 failures", idx.name);
            break;
        }

        let mut params = vec![
            ("query".to_string(), query),
            ("type".to_string(), search_type.to_string()),
            ("indexerIds".to_string(), idx.id.to_string()),
        ];
        for cat in &categories {
            params.push(("categories".to_string(), cat.to_string()));
        }

        match search_indexer(client, base_url, api_key, &params, query_timeout).await {
            Ok(mut items) => {
                consecutive_failures = 0;
                items.truncate(max_process);
                use futures::stream::{self, StreamExt};
                let mut batch: Vec<ScrapedStream> = stream::iter(items)
                    .map(|item| {
                        process_result(
                            client,
                            item,
                            &idx.name,
                            privacy_by_id,
                            media_type,
                            season,
                            episode,
                            query_timeout,
                        )
                    })
                    .buffer_unordered(RESULT_PROCESS_CONCURRENCY)
                    .filter_map(|result| async move { result })
                    .collect()
                    .await;
                results.append(&mut batch);
            }
            Err(e) => {
                consecutive_failures += 1;
                tracing::debug!(
                    "prowlarr: indexer {} failed: {}",
                    idx.name,
                    format_request_error(&*e)
                );
            }
        }
    }

    results
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

async fn fetch_indexers(
    client: &Client,
    base_url: &str,
    api_key: &str,
) -> Result<Vec<Indexer>, Box<dyn std::error::Error + Send + Sync>> {
    let (indexers_resp, statuses_resp) = tokio::join!(
        client
            .get(format!("{base_url}/api/v1/indexer"))
            .header("X-Api-Key", api_key)
            .timeout(Duration::from_secs(10))
            .send(),
        client
            .get(format!("{base_url}/api/v1/indexerstatus"))
            .header("X-Api-Key", api_key)
            .timeout(Duration::from_secs(10))
            .send()
    );

    let indexers: Vec<IndexerInfo> = indexers_resp?.json().await?;
    let statuses: Vec<IndexerStatus> = statuses_resp?.json().await.unwrap_or_default();

    let now = chrono::Utc::now();
    let disabled_ids: std::collections::HashSet<i64> = statuses
        .into_iter()
        .filter_map(|s| {
            let dt_str = s.disabled_till?;
            let dt_str = dt_str.replace('Z', "+00:00");
            let dt = chrono::DateTime::parse_from_rfc3339(&dt_str).ok()?;
            if dt > now { Some(s.indexer_id) } else { None }
        })
        .collect();

    let mut result: Vec<Indexer> = indexers
        .into_iter()
        .filter(|i| i.enable && !disabled_ids.contains(&i.id))
        .map(|i| {
            let cats: Vec<i64> = i
                .capabilities
                .categories
                .iter()
                .flat_map(|c| {
                    let mut ids = vec![c.id];
                    ids.extend(c.sub_categories.iter().map(|s| s.id));
                    ids
                })
                .collect();

            let supports_imdb_movie = !i.capabilities.movie_search_params.is_empty()
                && i.capabilities
                    .movie_search_params
                    .iter()
                    .any(|p| p.to_lowercase() == "imdbid");
            let supports_imdb_tv = !i.capabilities.tv_search_params.is_empty()
                && i.capabilities
                    .tv_search_params
                    .iter()
                    .any(|p| p.to_lowercase() == "imdbid");

            let supports_basic_search = !i.capabilities.search_params.is_empty();
            let is_public = i.privacy.to_lowercase() == "public";

            Indexer {
                id: i.id,
                name: i.name,
                priority: i.priority,
                is_public,
                privacy: i.privacy,
                categories: cats,
                supports_imdb_movie,
                supports_imdb_tv,
                supports_basic_search,
            }
        })
        .collect();

    result.sort_by(|a, b| {
        a.priority
            .cmp(&b.priority)
            .then_with(|| b.is_public.cmp(&a.is_public))
    });
    Ok(result)
}

fn movie_tv_categories(idx: &Indexer, is_tv: bool) -> Vec<i64> {
    let prefix = if is_tv { 5000_i64 } else { 2000_i64 };
    let relevant: Vec<i64> = idx
        .categories
        .iter()
        .copied()
        .filter(|&c| c / 1000 == prefix / 1000)
        .collect();
    if relevant.is_empty() {
        idx.categories.clone()
    } else {
        relevant
    }
}

async fn search_indexer(
    client: &Client,
    base_url: &str,
    api_key: &str,
    params: &[(String, String)],
    query_timeout: Duration,
) -> Result<Vec<SearchResult>, Box<dyn std::error::Error + Send + Sync>> {
    let resp = client
        .get(format!("{base_url}/api/v1/search"))
        .header("X-Api-Key", api_key)
        .query(params)
        .timeout(query_timeout)
        .send()
        .await?;

    resp.error_for_status_ref()?;
    let items: Vec<SearchResult> = resp.json().await?;
    Ok(items)
}

async fn process_result(
    client: &Client,
    item: SearchResult,
    indexer_name: &str,
    privacy_by_id: &HashMap<i64, String>,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    query_timeout: Duration,
) -> Option<ScrapedStream> {
    let title = item.title.as_deref().unwrap_or("").trim().to_string();
    if title.is_empty() {
        return None;
    }

    if !item.categories.is_empty() {
        let has_video = item.categories.iter().any(|c| {
            let id = c
                .get("id")
                .and_then(|v| v.as_i64())
                .unwrap_or_else(|| c.as_i64().unwrap_or(-1));
            (2000..3000).contains(&id) || (5000..6000).contains(&id)
        });
        if !has_video {
            return None;
        }
    }

    let indexer_privacy = item
        .indexer_id
        .and_then(|id| privacy_by_id.get(&id))
        .map(String::as_str)
        .unwrap_or("public");
    let torrent_type = prowlarr_torrent_type(&item.indexer_flags, indexer_privacy);

    let download_pick = resolve_download_url(
        torrent_type,
        item.guid.as_deref(),
        item.magnet_url.as_deref(),
        item.download_url.as_deref(),
    );

    let mut info_hash = item
        .info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40);
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
        && let Some(m) = item.magnet_url.as_deref()
    {
        info_hash = parser::extract_info_hash(m);
        if announce_list.is_empty() {
            announce_list = torrent_metadata::announce_list_from_magnet(m);
        }
    }

    let info_hash = info_hash?;

    let parsed = parser::parse_title(&title);
    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };

    let source = item
        .indexer
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| indexer_name.to_string());

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

pub(crate) fn resolve_result_info_hash(item: &SearchResult) -> Option<String> {
    item.info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40)
        .or_else(|| {
            item.magnet_url
                .as_deref()
                .and_then(parser::extract_info_hash)
        })
}

pub(crate) fn media_type_from_categories(categories: &[Value]) -> &'static str {
    for cat in categories {
        let id = cat
            .get("id")
            .and_then(|v| v.as_i64())
            .unwrap_or_else(|| cat.as_i64().unwrap_or(-1));
        if (2000..3000).contains(&id) {
            return "movie";
        }
        if (5000..6000).contains(&id) {
            return "series";
        }
    }
    "movie"
}

pub(crate) async fn fetch_indexer_privacy_map(
    client: &Client,
    base_url: &str,
    api_key: &str,
) -> HashMap<i64, String> {
    let indexers: Vec<IndexerInfo> = match client
        .get(format!("{base_url}/api/v1/indexer"))
        .header("X-Api-Key", api_key)
        .timeout(Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) => r.json().await.unwrap_or_default(),
        Err(_) => Vec::new(),
    };
    indexers.into_iter().map(|i| (i.id, i.privacy)).collect()
}

pub(crate) async fn process_feed_results(
    client: &Client,
    items: Vec<SearchResult>,
    privacy_by_id: &HashMap<i64, String>,
    query_timeout: Duration,
) -> Vec<(ScrapedStream, &'static str)> {
    use futures::stream::{self, StreamExt};
    stream::iter(items)
        .map(|item| {
            let media_type = media_type_from_categories(&item.categories);
            async move {
                let stream = process_result(
                    client,
                    item,
                    "Prowlarr",
                    privacy_by_id,
                    media_type,
                    None,
                    None,
                    query_timeout,
                )
                .await?;
                Some((stream, media_type))
            }
        })
        .buffer_unordered(RESULT_PROCESS_CONCURRENCY)
        .filter_map(|result| async move { result })
        .collect()
        .await
}

pub fn build_series_files(
    parsed: &crate::parser::ParsedTitle,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<StreamFile> {
    let seasons = if parsed.seasons.is_empty() {
        match season {
            Some(s) => vec![s],
            None => return vec![],
        }
    } else {
        parsed.seasons.clone()
    };

    let episodes = if parsed.episodes.is_empty() {
        match episode {
            Some(e) => vec![e],
            None => vec![1],
        }
    } else {
        parsed.episodes.clone()
    };

    let mut files = Vec::new();
    let mut idx: i32 = 0;
    for s in &seasons {
        for e in &episodes {
            files.push(StreamFile {
                file_index: idx,
                filename: String::new(),
                season_number: *s,
                episode_number: *e,
            });
            idx += 1;
        }
    }
    files
}

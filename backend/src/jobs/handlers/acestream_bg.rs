/// Background AceStream scraper (Python `run_acestream_background_scraper` parity).
use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::Value;
use tracing::{info, warn};

use crate::{
    db::{MediaId, MediaType, StreamId, StreamType},
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    routes::content::import_helpers::is_adult_content,
    scrapers::media_resolve,
};

const SEEN_KEY: &str = "acestream_bg:seen";
const SEEN_TTL: i64 = 604_800;
const FETCH_TIMEOUT_SECS: u64 = 20;
const MAX_ITEMS_PER_SOURCE: usize = 50;
const MAX_PAGES_PER_SOURCE: usize = 2;
const DEFAULT_QUERIES: &[&str] = &["live sports", "movies", "series"];

static ACESTREAM_URI_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"acestream://([a-fA-F0-9]{40})").expect("acestream uri regex"));

static ACESTREAM_ANCHOR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r#"<a[^>]*href=["']acestream://(?P<cid>[a-fA-F0-9]{40})["'][^>]*>(?P<title>[^<]+)</a>"#,
    )
    .expect("acestream anchor regex")
});

static INFOHASH_PARAM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:infohash|info_hash)=([a-fA-F0-9]{40})").expect("infohash param regex")
});

static LABELED_SERVER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"Server\s*(?P<server_no>\d+)\s*:\s*(?P<label>[^\n\r<]+).*?acestream://(?P<cid>[a-fA-F0-9]{40})",
    )
    .expect("acestream labeled server regex")
});

static RESOLUTION_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\b(\d{3,4}p)\b").expect("resolution regex"));

#[derive(Debug, Clone)]
struct AceCandidate {
    content_id: Option<String>,
    info_hash: Option<String>,
    title: Option<String>,
    source_name: String,
    default_media_type: String,
    channel_key: Option<String>,
    upsert_by_channel: bool,
    metadata_title: Option<String>,
    metadata_external_id: Option<String>,
    metadata_media_type: Option<String>,
    metadata_poster: Option<String>,
}

#[derive(Debug)]
struct AceStreamSourceConfig {
    name: String,
    urls: Vec<String>,
    media_type: String,
    channel_key: Option<String>,
    channel_key_mode: Option<String>,
    upsert_by_channel: bool,
    channel_name: Option<String>,
    labeled_server_parser: bool,
    metadata_title: Option<String>,
    metadata_external_id: Option<String>,
    metadata_media_type: Option<String>,
    metadata_poster: Option<String>,
}

fn scraper_config_root(path: &str) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn load_source_configs(path: &str) -> Vec<AceStreamSourceConfig> {
    let root = match scraper_config_root(path) {
        Some(v) => v,
        None => return vec![],
    };
    let items = root
        .get("acestream_background")
        .and_then(|v| v.get("sources"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let mut out = Vec::new();
    for item in items {
        if item.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
            continue;
        }
        let urls: Vec<String> = if let Some(arr) = item.get("urls").and_then(|v| v.as_array()) {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .filter(|s| !s.is_empty())
                .collect()
        } else if let Some(s) = item.get("url").and_then(|v| v.as_str()) {
            if s.is_empty() {
                vec![]
            } else {
                vec![s.to_string()]
            }
        } else {
            vec![]
        };
        if urls.is_empty() {
            continue;
        }
        let name = item
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&urls[0])
            .to_string();
        let target = item.get("target_metadata");
        out.push(AceStreamSourceConfig {
            name,
            urls,
            media_type: item
                .get("media_type")
                .and_then(|v| v.as_str())
                .unwrap_or("movie")
                .to_string(),
            channel_key: item
                .get("channel_key")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            channel_key_mode: item
                .get("channel_key_mode")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            upsert_by_channel: item
                .get("upsert_by_channel")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            channel_name: item
                .get("channel_name")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(str::to_string),
            labeled_server_parser: item
                .get("labeled_server_parser")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            metadata_title: target
                .and_then(|t| t.get("title"))
                .and_then(|v| v.as_str())
                .map(str::to_string),
            metadata_external_id: target
                .and_then(|t| t.get("id"))
                .and_then(|v| v.as_str())
                .map(str::to_string),
            metadata_media_type: target
                .and_then(|t| t.get("media_type"))
                .and_then(|v| v.as_str())
                .map(str::to_string),
            metadata_poster: target
                .and_then(|t| t.get("poster"))
                .and_then(|v| v.as_str())
                .map(str::to_string),
        });
    }
    out
}

fn clean_acestream_stream_name(raw: &str) -> (String, Option<String>) {
    let mut label = Regex::new(r"(?i)^\s*Server\s*\d+\s*:\s*")
        .unwrap()
        .replace(raw, "")
        .trim()
        .to_string();
    label = Regex::new(r"(?i)/\s*\d+\s*fps\b")
        .unwrap()
        .replace_all(&label, "")
        .trim()
        .to_string();
    label = label.split_whitespace().collect::<Vec<_>>().join(" ");
    let resolution = RESOLUTION_RE
        .find(&label)
        .map(|m| m.as_str().to_lowercase());
    let lower = label.to_lowercase();
    if lower.contains("f1tv") {
        if let Some(ref res) = resolution {
            return (format!("F1TV {res}"), resolution);
        }
    }
    if lower.contains("sky sport f1") {
        return ("Sky Sport F1".to_string(), resolution);
    }
    if lower == "skyf1" {
        return ("SKYF1".to_string(), resolution);
    }
    if let Some(cap) = Regex::new(r"(?i)\bdanz\s+server\s+(\d+)\b")
        .unwrap()
        .captures(&lower)
    {
        return (format!("DAZN {}", &cap[1]), resolution);
    }
    (
        if label.is_empty() {
            "F1 Live".to_string()
        } else {
            label
        },
        resolution,
    )
}

fn source_identifier(source_name: &str, channel_key: Option<&str>) -> String {
    if let Some(ck) = channel_key.filter(|s| !s.is_empty()) {
        format!("acestream:{source_name}:{ck}")
    } else {
        source_name.to_string()
    }
}

fn extract_from_html(body: &str, source_name: &str, default_media_type: &str) -> Vec<AceCandidate> {
    let mut candidates = Vec::new();
    for cap in ACESTREAM_ANCHOR_RE.captures_iter(body) {
        let cid = cap.name("cid").map(|m| m.as_str().to_lowercase());
        let title = cap
            .name("title")
            .map(|m| m.as_str().trim().to_string())
            .filter(|s| !s.is_empty());
        if let Some(content_id) = cid {
            candidates.push(AceCandidate {
                content_id: Some(content_id),
                info_hash: None,
                title,
                source_name: source_name.to_string(),
                default_media_type: default_media_type.to_string(),
                channel_key: None,
                upsert_by_channel: false,
                metadata_title: None,
                metadata_external_id: None,
                metadata_media_type: None,
                metadata_poster: None,
            });
        }
    }
    for cap in ACESTREAM_URI_RE.captures_iter(body) {
        let cid = cap[1].to_lowercase();
        if candidates
            .iter()
            .any(|c| c.content_id.as_deref() == Some(&cid))
        {
            continue;
        }
        candidates.push(AceCandidate {
            content_id: Some(cid),
            info_hash: None,
            title: None,
            source_name: source_name.to_string(),
            default_media_type: default_media_type.to_string(),
            channel_key: None,
            upsert_by_channel: false,
            metadata_title: None,
            metadata_external_id: None,
            metadata_media_type: None,
            metadata_poster: None,
        });
    }
    for cap in INFOHASH_PARAM_RE.captures_iter(body) {
        candidates.push(AceCandidate {
            content_id: None,
            info_hash: Some(cap[1].to_lowercase()),
            title: None,
            source_name: source_name.to_string(),
            default_media_type: default_media_type.to_string(),
            channel_key: None,
            upsert_by_channel: false,
            metadata_title: None,
            metadata_external_id: None,
            metadata_media_type: None,
            metadata_poster: None,
        });
    }
    candidates
}

fn extract_labeled_servers(
    body: &str,
    source_name: &str,
    default_media_type: &str,
) -> Vec<AceCandidate> {
    let mut candidates = Vec::new();
    for cap in LABELED_SERVER_RE.captures_iter(body) {
        let content_id = cap.name("cid").map(|m| m.as_str().to_lowercase());
        let server_no = cap.name("server_no").map(|m| m.as_str()).unwrap_or("");
        let label = cap.name("label").map(|m| m.as_str().trim()).unwrap_or("");
        let display = if server_no.is_empty() {
            label.to_string()
        } else {
            format!("Server {server_no}: {label}")
        };
        if let Some(cid) = content_id {
            candidates.push(AceCandidate {
                content_id: Some(cid),
                info_hash: None,
                title: Some(display),
                source_name: source_name.to_string(),
                default_media_type: default_media_type.to_string(),
                channel_key: None,
                upsert_by_channel: false,
                metadata_title: None,
                metadata_external_id: None,
                metadata_media_type: None,
                metadata_poster: None,
            });
        }
    }
    candidates
}

fn json_items(payload: &Value) -> Vec<&Value> {
    if let Some(arr) = payload.as_array() {
        return arr.iter().collect();
    }
    for key in ["results", "items", "data", "streams"] {
        if let Some(arr) = payload.get(key).and_then(|v| v.as_array()) {
            return arr.iter().collect();
        }
    }
    vec![]
}

fn extract_from_json_item(
    item: &Value,
    source_name: &str,
    default_media_type: &str,
) -> Vec<AceCandidate> {
    let mut out = Vec::new();
    let title = item
        .get("title")
        .or_else(|| item.get("name"))
        .and_then(|v| v.as_str())
        .map(str::to_string);
    for key in ["content_id", "contentId", "acestream_id", "id"] {
        if let Some(v) = item.get(key).and_then(|x| x.as_str()) {
            if v.contains("acestream://") {
                if let Some(cap) = ACESTREAM_URI_RE.captures(v) {
                    out.push(AceCandidate {
                        content_id: Some(cap[1].to_lowercase()),
                        info_hash: None,
                        title: title.clone(),
                        source_name: source_name.to_string(),
                        default_media_type: default_media_type.to_string(),
                        channel_key: None,
                        upsert_by_channel: false,
                        metadata_title: None,
                        metadata_external_id: None,
                        metadata_media_type: None,
                        metadata_poster: None,
                    });
                }
            } else if v.len() == 40 && v.chars().all(|c| c.is_ascii_hexdigit()) {
                out.push(AceCandidate {
                    content_id: Some(v.to_lowercase()),
                    info_hash: None,
                    title: title.clone(),
                    source_name: source_name.to_string(),
                    default_media_type: default_media_type.to_string(),
                    channel_key: None,
                    upsert_by_channel: false,
                    metadata_title: None,
                    metadata_external_id: None,
                    metadata_media_type: None,
                    metadata_poster: None,
                });
            }
        }
    }
    for key in ["info_hash", "infoHash"] {
        if let Some(v) = item.get(key).and_then(|x| x.as_str()) {
            if v.len() == 40 {
                out.push(AceCandidate {
                    content_id: None,
                    info_hash: Some(v.to_lowercase()),
                    title: title.clone(),
                    source_name: source_name.to_string(),
                    default_media_type: default_media_type.to_string(),
                    channel_key: None,
                    upsert_by_channel: false,
                    metadata_title: None,
                    metadata_external_id: None,
                    metadata_media_type: None,
                    metadata_poster: None,
                });
            }
        }
    }
    out
}

async fn fetch_search_api_candidates(
    http: &reqwest::Client,
    config_path: &str,
    api_key: Option<&str>,
) -> Vec<AceCandidate> {
    let root = match scraper_config_root(config_path) {
        Some(v) => v,
        None => return vec![],
    };
    let search = match root
        .get("acestream_background")
        .and_then(|v| v.get("search_api"))
    {
        Some(s) => s,
        None => return vec![],
    };
    if !search
        .get("enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        return vec![];
    }
    let url = search
        .get("url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if url.is_empty() {
        return vec![];
    }
    let source_name = search
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("acestream_search_api");
    let default_media_type = search
        .get("media_type")
        .and_then(|v| v.as_str())
        .unwrap_or("movie");
    let queries: Vec<String> = search
        .get("queries")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .filter(|s| !s.is_empty())
                .collect()
        })
        .filter(|v: &Vec<String>| !v.is_empty())
        .unwrap_or_else(|| DEFAULT_QUERIES.iter().map(|s| s.to_string()).collect());

    let query_param = search
        .get("query_param")
        .and_then(|v| v.as_str())
        .unwrap_or("query");
    let page_param = search
        .get("page_param")
        .and_then(|v| v.as_str())
        .unwrap_or("page");
    let limit_param = search
        .get("limit_param")
        .and_then(|v| v.as_str())
        .unwrap_or("limit");
    let max_results = search
        .get("max_results")
        .and_then(|v| v.as_u64())
        .unwrap_or(MAX_ITEMS_PER_SOURCE as u64) as usize;
    let max_pages = search
        .get("max_pages")
        .and_then(|v| v.as_u64())
        .unwrap_or(MAX_PAGES_PER_SOURCE as u64) as usize;
    let max_results = max_results.clamp(1, MAX_ITEMS_PER_SOURCE);
    let max_pages = max_pages.clamp(1, MAX_PAGES_PER_SOURCE);

    let mut candidates = Vec::new();
    for query in queries {
        for page in 1..=max_pages {
            let page_s = page.to_string();
            let limit_s = max_results.to_string();
            let mut req = http
                .get(url)
                .query(&[
                    (query_param, query.as_str()),
                    (page_param, page_s.as_str()),
                    (limit_param, limit_s.as_str()),
                ])
                .timeout(std::time::Duration::from_secs(FETCH_TIMEOUT_SECS));
            if let Some(key) = api_key {
                if let Some(header) = search.get("api_key_header").and_then(|v| v.as_str()) {
                    let prefix = search
                        .get("api_key_prefix")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    req = req.header(header, format!("{prefix}{key}"));
                }
                if let Some(param) = search.get("api_key_param").and_then(|v| v.as_str()) {
                    req = req.query(&[(param, key)]);
                }
            }
            let payload: Value = match req.send().await {
                Ok(r) if r.status().is_success() => r.json().await.unwrap_or(Value::Null),
                _ => break,
            };
            let items = json_items(&payload);
            if items.is_empty() {
                break;
            }
            let mut produced = 0usize;
            for item in items {
                for c in extract_from_json_item(item, source_name, default_media_type) {
                    candidates.push(c);
                    produced += 1;
                    if produced >= max_results {
                        break;
                    }
                }
            }
            if produced == 0 {
                break;
            }
        }
    }
    candidates
}

async fn fetch_source_candidates(
    http: &reqwest::Client,
    configs: &[AceStreamSourceConfig],
) -> Vec<AceCandidate> {
    let mut all = Vec::new();
    for cfg in configs {
        for url in cfg.urls.iter().take(MAX_PAGES_PER_SOURCE) {
            let body = match http
                .get(url)
                .timeout(std::time::Duration::from_secs(FETCH_TIMEOUT_SECS))
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
                Err(e) => {
                    warn!("acestream_bg: fetch {} failed: {e}", url);
                    continue;
                }
                Ok(r) => {
                    warn!("acestream_bg: HTTP {} from {}", r.status().as_u16(), url);
                    continue;
                }
            };
            let extracted = if cfg.labeled_server_parser {
                extract_labeled_servers(&body, &cfg.name, &cfg.media_type)
            } else {
                extract_from_html(&body, &cfg.name, &cfg.media_type)
            };
            for mut c in extracted.into_iter().take(MAX_ITEMS_PER_SOURCE) {
                if let Some(ref cn) = cfg.channel_name {
                    c.title = Some(cn.clone());
                }
                c.metadata_title = cfg.metadata_title.clone();
                c.metadata_external_id = cfg.metadata_external_id.clone();
                c.metadata_media_type = cfg.metadata_media_type.clone();
                c.metadata_poster = cfg.metadata_poster.clone();
                if cfg.upsert_by_channel {
                    if let Some(ref mode) = cfg.channel_key_mode {
                        c.channel_key = Some(match mode.as_str() {
                            "server_label" => {
                                format!("{}:{}", cfg.name, c.title.as_deref().unwrap_or("stream"))
                            }
                            "server_number" => format!("{}:server:0", cfg.name),
                            _ => cfg.channel_key.clone().unwrap_or_default(),
                        });
                    } else {
                        c.channel_key = cfg.channel_key.clone();
                    }
                    c.upsert_by_channel = c.channel_key.is_some();
                }
                all.push(c);
            }
        }
    }
    all
}

async fn ensure_tv_media(
    pool: &sqlx::PgPool,
    title: &str,
    poster: Option<&str>,
) -> Option<MediaId> {
    let existing: Option<MediaId> = sqlx::query_scalar(
        "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = $2 LIMIT 1",
    )
    .bind(title)
    .bind(MediaType::Tv)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()?;
    if let Some(id) = existing {
        if let Some(p) = poster {
            let _ = sqlx::query(
                "UPDATE media SET poster = $1 WHERE id = $2 AND (poster IS NULL OR poster = '')",
            )
            .bind(p)
            .bind(id)
            .execute(pool)
            .await;
        }
        return Some(id);
    }
    let id: Option<MediaId> = sqlx::query_scalar(
        "INSERT INTO media (title, type, created_at, adult, is_blocked, is_public, is_user_created, nudity_status, total_streams, popularity) \
         VALUES ($1, $2, NOW(), false, false, true, false, 'UNKNOWN', 0, 0.0) RETURNING id",
    )
    .bind(title)
    .bind(MediaType::Tv)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()?;
    if let (Some(mid), Some(p)) = (id, poster) {
        let _ = sqlx::query(
            "UPDATE media SET poster = $1 WHERE id = $2 AND (poster IS NULL OR poster = '')",
        )
        .bind(p)
        .bind(mid)
        .execute(pool)
        .await;
    }
    id
}

async fn find_by_content_id(pool: &sqlx::PgPool, content_id: &str) -> Option<StreamId> {
    sqlx::query_scalar("SELECT stream_id FROM acestream_stream WHERE content_id = $1 LIMIT 1")
        .bind(content_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
}

async fn find_by_source(pool: &sqlx::PgPool, source: &str) -> Option<(StreamId, String)> {
    sqlx::query_as(
        "SELECT s.id, ac.content_id FROM stream s \
         JOIN acestream_stream ac ON ac.stream_id = s.id \
         WHERE s.source = $1 AND s.stream_type = $2 LIMIT 1",
    )
    .bind(source)
    .bind(StreamType::Acestream)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

#[allow(clippy::too_many_arguments)]
async fn upsert_acestream(
    pool: &sqlx::PgPool,
    name: &str,
    source: &str,
    resolution: Option<&str>,
    release_group: Option<&str>,
    content_id: &str,
    info_hash: Option<&str>,
    media_id: Option<MediaId>,
) -> Result<Option<StreamId>, sqlx::Error> {
    if let Some(existing) = find_by_content_id(pool, content_id).await {
        sqlx::query(
            "UPDATE stream SET name = $1, source = $2, resolution = $3, release_group = $4, updated_at = NOW() WHERE id = $5",
        )
        .bind(name)
        .bind(source)
        .bind(resolution)
        .bind(release_group)
        .bind(existing)
        .execute(pool)
        .await?;
        sqlx::query(
            "UPDATE acestream_stream SET info_hash = COALESCE($1, info_hash) WHERE stream_id = $2",
        )
        .bind(info_hash)
        .bind(existing)
        .execute(pool)
        .await?;
        if let Some(mid) = media_id {
            let _ = media_resolve::link_stream_to_media(pool, existing, mid).await;
        }
        return Ok(Some(existing));
    }

    let base = crate::db::StreamStoreBase {
        name: name.to_string(),
        source: source.to_string(),
        resolution: resolution.map(str::to_string),
        release_group: release_group.map(str::to_string),
        is_public: true,
        ..Default::default()
    };

    let normalized = crate::db::AcestreamStoreInput {
        base,
        content_id: content_id.to_string(),
        info_hash: info_hash.map(str::to_string),
    };

    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: MediaId(0),
            media_type: crate::db::MediaType::Series,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::PttParser,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::scraper(mid, crate::db::MediaType::Series),
    );

    let result = crate::db::store_acestream_stream(pool, &normalized, &opts).await?;
    Ok(Some(result.stream_id()))
}

async fn process_candidate(
    pool: &sqlx::PgPool,
    http: &reqwest::Client,
    candidate: &AceCandidate,
    cfg: &crate::config::AppConfig,
) -> Result<&'static str, sqlx::Error> {
    let content_id = match &candidate.content_id {
        Some(c) => c.clone(),
        None => return Ok("skipped"),
    };

    let default_title = format!("AceStream {}", &content_id[..10.min(content_id.len())]);
    let raw_title = candidate.title.as_deref().unwrap_or(&default_title);
    let (title, resolution) = clean_acestream_stream_name(raw_title);
    if is_adult_content(&title) {
        return Ok("skipped");
    }

    let source_id = source_identifier(&candidate.source_name, candidate.channel_key.as_deref());

    if candidate.upsert_by_channel {
        if let Some((_stream_id, existing_cid)) = find_by_source(pool, &source_id).await {
            if existing_cid == content_id {
                return Ok("skipped");
            }
            let media_id = resolve_media_for_candidate(pool, http, candidate, &title, cfg).await;
            let _ = upsert_acestream(
                pool,
                &title,
                &source_id,
                resolution.as_deref(),
                candidate.channel_key.as_deref(),
                &content_id,
                candidate.info_hash.as_deref(),
                media_id,
            )
            .await?;
            return Ok("updated");
        }
    } else if find_by_content_id(pool, &content_id).await.is_some() {
        return Ok("skipped");
    }

    let media_id = resolve_media_for_candidate(pool, http, candidate, &title, cfg).await;
    if upsert_acestream(
        pool,
        &title,
        &source_id,
        resolution.as_deref(),
        candidate.channel_key.as_deref(),
        &content_id,
        candidate.info_hash.as_deref(),
        media_id,
    )
    .await?
    .is_some()
    {
        Ok("created")
    } else {
        Ok("skipped")
    }
}

async fn resolve_media_for_candidate(
    pool: &sqlx::PgPool,
    http: &reqwest::Client,
    candidate: &AceCandidate,
    title: &str,
    cfg: &crate::config::AppConfig,
) -> Option<MediaId> {
    let media_type = candidate
        .metadata_media_type
        .as_deref()
        .unwrap_or(candidate.default_media_type.as_str());
    if media_type == "tv" {
        let meta_title = candidate.metadata_title.as_deref().unwrap_or(title);
        return ensure_tv_media(pool, meta_title, candidate.metadata_poster.as_deref()).await;
    }

    let parsed = parser::parse_title(title);
    let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();
    let meta = media_resolve::search_meta_for_title_with_anime(
        pool,
        http,
        parsed.title.as_deref().unwrap_or(title),
        parsed.year,
        is_series,
        cfg.tmdb_api_key.as_deref(),
        cfg.imdb_cinemeta_fallback_enabled,
        &cfg.anime_metadata_source_order,
        &cfg.metadata_primary_source,
    )
    .await?;
    Some(meta.media_id)
}

fn dedupe_key(candidate: &AceCandidate) -> String {
    if candidate.upsert_by_channel {
        if let Some(ref ck) = candidate.channel_key {
            return format!(
                "channel:{ck}:{}:{}",
                candidate.content_id.as_deref().unwrap_or(""),
                candidate.info_hash.as_deref().unwrap_or("")
            );
        }
    }
    format!(
        "{}:{}",
        candidate.content_id.as_deref().unwrap_or(""),
        candidate.info_hash.as_deref().unwrap_or("")
    )
}

pub struct AcestreamBgScraper;

#[async_trait]
impl JobHandler for AcestreamBgScraper {
    const QUEUE: &'static str = "acestream_bg";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config_path = &ctx.state.config.scraper_config_path;
        let source_configs = load_source_configs(config_path);
        let api_key = std::env::var("ACESTREAM_BACKGROUND_SEARCH_API_KEY")
            .ok()
            .filter(|s| !s.is_empty());

        let mut candidates =
            fetch_search_api_candidates(&ctx.state.http, config_path, api_key.as_deref()).await;
        candidates.extend(fetch_source_candidates(&ctx.state.http, &source_configs).await);

        if candidates.is_empty() {
            info!("acestream_bg: no candidates");
            return Ok(());
        }

        let mut seen_keys = std::collections::HashSet::new();
        candidates.retain(|c| seen_keys.insert(dedupe_key(c)));

        info!(
            "acestream_bg: processing {} unique candidates",
            candidates.len()
        );

        let pool = &ctx.state.pool;
        let redis = &ctx.state.redis;
        let mut metrics = (0usize, 0usize, 0usize, 0usize); // processed, created, updated, skipped

        for candidate in candidates {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            let item_key = format!("acestream:{}", dedupe_key(&candidate));
            if redis
                .sismember::<bool, _, _>(SEEN_KEY, &item_key)
                .await
                .unwrap_or(false)
            {
                metrics.3 += 1;
                continue;
            }
            metrics.0 += 1;
            match process_candidate(pool, &ctx.state.http, &candidate, &ctx.state.config).await {
                Ok("created") => metrics.1 += 1,
                Ok("updated") => metrics.2 += 1,
                Ok(_) => metrics.3 += 1,
                Err(e) => {
                    warn!("acestream_bg: process error: {e}");
                    metrics.3 += 1;
                }
            }
            let _ = redis.sadd::<(), _, _>(SEEN_KEY, item_key).await;
            let _ = redis.expire::<i64, _>(SEEN_KEY, SEEN_TTL, None).await;
        }

        info!(
            "acestream_bg: done processed={} created={} updated={} skipped={}",
            metrics.0, metrics.1, metrics.2, metrics.3
        );
        Ok(())
    }
}

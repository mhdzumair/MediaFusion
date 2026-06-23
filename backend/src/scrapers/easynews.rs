/// Easynews Usenet scraper.
///
/// Mirrors Python `scrapers/easynews.py` + `streaming_providers/easynews/client.py`.
/// Uses HTTP Basic Auth against the Easynews Solr search API.
/// Results carry direct streaming URLs (credentials embedded) — always "cached".
use std::sync::OnceLock;

use reqwest::Client;
use sha2::{Digest, Sha256};

use crate::{
    parser,
    scrapers::{ScrapedUsenetStream, SearchMeta, prowlarr::build_series_files},
    state::KeywordFilterCache,
};

const SEARCH_URL: &str = "https://members.easynews.com/2.0/search/solr-search/advanced";
const MIN_MOVIE_SIZE: i64 = 300 * 1024 * 1024; // 300 MB
const MOVIE_SIMILARITY_MIN: u32 = 88;
const SERIES_SIMILARITY_MIN: u32 = 80;

static TV_EP_RE: OnceLock<regex::Regex> = OnceLock::new();

fn tv_ep_re() -> &'static regex::Regex {
    TV_EP_RE.get_or_init(|| regex::Regex::new(r"(?i)\bS\d{1,2}[-.\s]?E\d{1,3}\b").unwrap())
}

pub async fn scrape(
    client: &Client,
    username: &str,
    password: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedUsenetStream> {
    let mut results: Vec<ScrapedUsenetStream> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    let queries: Vec<(String, usize)> = if media_type == "series" {
        match (season, episode) {
            (Some(s), Some(e)) => vec![
                (format!("{} S{s:02}E{e:02}", meta.title), 80),
                (format!("{} s{s:02}e{e:02}", meta.title), 80),
                (format!("{} {s}x{e:02}", meta.title), 80),
            ],
            _ => vec![(meta.title.clone(), 50)],
        }
    } else {
        // Movies: try with IMDb ID first (narrow but precise), then year, then bare title
        let mut qs = Vec::new();
        if let Some(ref imdb_id) = meta.imdb_id {
            qs.push((format!("{} {imdb_id}", meta.title), 100));
        }
        if let Some(y) = meta.year {
            qs.push((format!("{} {y}", meta.title), 100));
        }
        qs.push((meta.title.clone(), 100));
        qs
    };

    for (query, max_results) in queries {
        match search(client, username, password, &query, max_results).await {
            Err(SearchError::AuthFailed) => {
                // 401 — credentials are invalid; no point trying the remaining queries.
                tracing::debug!(
                    "easynews: credentials rejected (401) for user '{}' — skipping all queries",
                    username
                );
                return vec![];
            }
            Err(SearchError::Other) => {
                // Transient error (network, 5xx, etc.) — skip this query variant, try next.
                continue;
            }
            Ok(items) => {
                for item in items {
                    let guid = &item.nzb_guid;
                    if seen.insert(guid.clone()) {
                        if let Some(stream) =
                            parse_item(item, meta, media_type, season, episode, keyword_filters)
                        {
                            results.push(stream);
                        }
                    }
                }
            }
        }
    }

    results
}

// ─── Internal search ──────────────────────────────────────────────────────────

enum SearchError {
    /// HTTP 401 — credentials are wrong; caller should stop all further queries.
    AuthFailed,
    /// Any other failure (network, 5xx, parse) — caller may try the next query.
    Other,
}

struct RawItem {
    nzb_guid: String,
    file_id: String,
    filename: String,
    size: i64,
    group: Option<String>,
    sig: Option<String>,
    down_url: Option<String>,
    dl_farm: Option<String>,
    dl_port: Option<String>,
    file_hash: Option<String>,
    file_title: Option<String>,
    file_extension: Option<String>,
}

async fn search(
    client: &Client,
    username: &str,
    password: &str,
    query: &str,
    max_results: usize,
) -> Result<Vec<RawItem>, SearchError> {
    let params = [
        ("st", "adv"),
        ("sb", "1"),
        (
            "fex",
            "m4v,3gp,mov,divx,xvid,wmv,avi,mpg,mpeg,mp4,mkv,avc,flv,webm",
        ),
        ("spamf", "1"),
        ("u", "1"),
        ("gx", "1"),
        ("pno", "1"),
        ("sS", "3"),
        ("s1", "relevance"),
        ("s1d", "-"),
        ("s2", "dsize"),
        ("s2d", "-"),
        ("s3", "dtime"),
        ("s3d", "-"),
        ("safeO", "0"),
        ("gps", query),
        ("fty[]", "VIDEO"),
    ];
    let pby = max_results.to_string();

    let resp = client
        .get(SEARCH_URL)
        .basic_auth(username, Some(password))
        .query(&params)
        .query(&[("pby", pby.as_str())])
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await;

    let json: serde_json::Value = match resp {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
        Ok(r) if r.status() == reqwest::StatusCode::UNAUTHORIZED => {
            tracing::debug!("easynews search '{query}': HTTP 401 Unauthorized");
            return Err(SearchError::AuthFailed);
        }
        Ok(r) => {
            tracing::debug!("easynews search '{query}': HTTP {}", r.status());
            return Err(SearchError::Other);
        }
        Err(e) => {
            tracing::debug!("easynews search '{query}': {e}");
            return Err(SearchError::Other);
        }
    };

    let down_url = json
        .get("downURL")
        .and_then(|v| v.as_str())
        .map(str::to_owned);
    let dl_farm = json
        .get("dlFarm")
        .and_then(|v| v.as_str())
        .map(str::to_owned);
    let dl_port = json.get("dlPort").and_then(|v| {
        v.as_str()
            .map(str::to_owned)
            .or_else(|| v.as_u64().map(|n| n.to_string()))
    });

    let data = match json.get("data").and_then(|d| d.as_array()) {
        Some(arr) => arr,
        None => return Ok(vec![]),
    };

    let mut items = Vec::new();
    for item in data {
        let file_id = item
            .get("0")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        if file_id.is_empty() {
            continue;
        }

        let nzb_guid = sha256_prefix_40(&format!("easynews:{file_id}"));

        let extension = item
            .get("11")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let post_title = item
            .get("10")
            .or_else(|| item.get("fn"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let filename = if !post_title.is_empty() {
            if !extension.is_empty()
                && !post_title
                    .to_lowercase()
                    .ends_with(&extension.to_lowercase())
            {
                format!("{post_title}{extension}")
            } else {
                post_title.clone()
            }
        } else {
            item.get("2")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        };

        let size = item
            .get("rawSize")
            .or_else(|| item.get("4"))
            .and_then(|v| {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .unwrap_or(0);

        let group = item.get("6").and_then(|v| v.as_str()).map(str::to_owned);
        let sig = item.get("sig").and_then(|v| v.as_str()).map(str::to_owned);
        let file_hash = item.get("0").and_then(|v| v.as_str()).map(str::to_owned);

        items.push(RawItem {
            nzb_guid,
            file_id,
            filename,
            size,
            group,
            sig,
            down_url: down_url.clone(),
            dl_farm: dl_farm.clone(),
            dl_port: dl_port.clone(),
            file_hash,
            file_title: if post_title.is_empty() {
                None
            } else {
                Some(post_title)
            },
            file_extension: if extension.is_empty() {
                None
            } else {
                Some(extension)
            },
        });
    }

    Ok(items)
}

fn parse_item(
    item: RawItem,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Option<ScrapedUsenetStream> {
    if item.filename.is_empty() {
        return None;
    }
    if keyword_filters.matches_blocked_keyword(&item.filename) {
        return None;
    }

    let parsed = parser::parse_title(&item.filename);

    // Skip samples
    if item.filename.to_lowercase().contains("sample") {
        return None;
    }

    if media_type == "movie" {
        // Reject TV episode patterns in movie results
        if tv_ep_re().is_match(&item.filename) {
            return None;
        }
        // Reject if PTT found season/episode numbers (false positives like "204" → ep)
        if !parsed.seasons.is_empty() || !parsed.episodes.is_empty() {
            return None;
        }
        // Minimum size gate
        if item.size < MIN_MOVIE_SIZE {
            return None;
        }
        // Year validation
        if let (Some(py), Some(my)) = (parsed.year, meta.year) {
            if py != my {
                return None;
            }
        }
    }

    // Title similarity check
    let sim_min = if media_type == "movie" {
        MOVIE_SIMILARITY_MIN
    } else {
        SERIES_SIMILARITY_MIN
    };
    let ratio = parser::similarity_ratio(
        parsed.title.as_deref().unwrap_or(&item.filename),
        &meta.title,
    );
    if ratio < sim_min {
        return None;
    }

    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };

    if media_type == "series" && files.is_empty() {
        return None;
    }

    let nzb_url = generate_download_url(
        // Username/password are not available here — they're embedded at the search layer.
        // The URL is built by the orchestrator which passes credentials.
        // We store a placeholder; the orchestrator calls scrape() with credentials and then
        // calls this after URL building (see below).
        "",
        "",
        &item.file_id,
        &item.filename,
        item.sig.as_deref(),
        item.down_url.as_deref(),
        item.dl_farm.as_deref(),
        item.dl_port.as_deref(),
        item.file_hash.as_deref(),
        item.file_title.as_deref(),
        item.file_extension.as_deref(),
    );

    Some(ScrapedUsenetStream {
        nzb_guid: item.nzb_guid,
        nzb_url,
        name: item.filename,
        size: item.size,
        indexer: "Easynews".to_string(),
        source: "Easynews".to_string(),
        group_name: item.group,
        parsed,
        files,
        is_cached: true,
    })
}

// ─── Public entry point with credential injection ─────────────────────────────

/// Full scrape with credentials injected into download URLs.
pub async fn scrape_with_credentials(
    client: &Client,
    username: &str,
    password: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedUsenetStream> {
    let queries: Vec<(String, usize)> = if media_type == "series" {
        match (season, episode) {
            (Some(s), Some(e)) => vec![
                (format!("{} S{s:02}E{e:02}", meta.title), 80),
                (format!("{} s{s:02}e{e:02}", meta.title), 80),
                (format!("{} {s}x{e:02}", meta.title), 80),
            ],
            _ => vec![(meta.title.clone(), 50)],
        }
    } else {
        let mut qs = Vec::new();
        if let Some(ref imdb_id) = meta.imdb_id {
            qs.push((format!("{} {imdb_id}", meta.title), 100));
        }
        if let Some(y) = meta.year {
            qs.push((format!("{} {y}", meta.title), 100));
        }
        qs.push((meta.title.clone(), 100));
        qs
    };

    let mut results: Vec<ScrapedUsenetStream> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    for (query, max_results) in queries {
        let raw_items = match search(client, username, password, &query, max_results).await {
            Err(SearchError::AuthFailed) => {
                tracing::debug!(
                    "easynews: credentials rejected (401) for user '{}' — skipping all queries",
                    username
                );
                return vec![];
            }
            Err(SearchError::Other) => continue,
            Ok(items) => items,
        };
        for mut item in raw_items {
            if !seen.insert(item.nzb_guid.clone()) {
                continue;
            }
            // Re-build URL with real credentials
            let nzb_url = generate_download_url(
                username,
                password,
                &item.file_id.clone(),
                &item.filename.clone(),
                item.sig.as_deref(),
                item.down_url.as_deref(),
                item.dl_farm.as_deref(),
                item.dl_port.as_deref(),
                item.file_hash.as_deref(),
                item.file_title.as_deref(),
                item.file_extension.as_deref(),
            );
            item.down_url = None; // avoid leaking in later stages
            if let Some(mut stream) =
                parse_item(item, meta, media_type, season, episode, keyword_filters)
            {
                stream.nzb_url = nzb_url;
                results.push(stream);
            }
        }
    }

    results
}

// ─── URL helpers ──────────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn generate_download_url(
    username: &str,
    password: &str,
    file_id: &str,
    filename: &str,
    sig: Option<&str>,
    down_url: Option<&str>,
    dl_farm: Option<&str>,
    dl_port: Option<&str>,
    file_hash: Option<&str>,
    file_title: Option<&str>,
    file_extension: Option<&str>,
) -> String {
    // If server-provided farm URL fields are available, use them.
    if let (Some(down), Some(farm), Some(port), Some(hash), Some(title)) =
        (down_url, dl_farm, dl_port, file_hash, file_title)
    {
        if !down.is_empty() && !farm.is_empty() && !port.is_empty() {
            let ext = file_extension.unwrap_or("");
            let ext_dot = if !ext.is_empty() && !ext.starts_with('.') {
                format!(".{ext}")
            } else {
                ext.to_string()
            };
            let file_path = format!("{hash}{ext_dot}/{title}{ext_dot}");
            let encoded_path: String = file_path
                .split('/')
                .map(url_encode_component)
                .collect::<Vec<_>>()
                .join("/");
            return format!(
                "{}/{}/{}/{}",
                inject_auth(down.trim_end_matches('/'), username, password),
                url_encode_component(farm),
                url_encode_component(port),
                encoded_path,
            );
        }
    }

    // Legacy URL
    let effective_filename = match (filename.is_empty(), file_title, file_extension) {
        (true, Some(title), Some(ext)) => {
            let ext_dot = if ext.starts_with('.') {
                ext.to_string()
            } else {
                format!(".{ext}")
            };
            format!("{title}{ext_dot}")
        }
        _ => filename.to_string(),
    };

    let enc_user = url_encode_component(username);
    let enc_pass = url_encode_component(password);
    let enc_file = url_encode_component(&effective_filename);
    let base = format!("https://{enc_user}:{enc_pass}@members.easynews.com");

    if let Some(s) = sig {
        format!("{base}/dl/{file_id}/{enc_file}?sig={s}")
    } else {
        format!("{base}/dl/{file_id}/{enc_file}")
    }
}

fn inject_auth(url: &str, username: &str, password: &str) -> String {
    if username.is_empty() {
        return url.to_string();
    }
    let enc_user = url_encode_component(username);
    let enc_pass = url_encode_component(password);
    if let Some(rest) = url.strip_prefix("https://") {
        format!("https://{enc_user}:{enc_pass}@{rest}")
    } else if let Some(rest) = url.strip_prefix("http://") {
        format!("http://{enc_user}:{enc_pass}@{rest}")
    } else {
        format!(
            "https://{enc_user}:{enc_pass}@{}",
            url.trim_start_matches('/')
        )
    }
}

fn url_encode_component(s: &str) -> String {
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

fn sha256_prefix_40(input: &str) -> String {
    let hash = Sha256::digest(input.as_bytes());
    hash.iter().map(|b| format!("{b:02x}")).collect::<String>()[..40].to_string()
}

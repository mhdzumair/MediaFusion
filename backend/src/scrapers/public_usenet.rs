/// Public Usenet indexer scrapers: NZBIndex (JSON API) and Binsearch (HTML).
///
/// NZBIndex exposes a proper JSON search API — straightforward to consume.
/// Binsearch returns HTML; we parse it with regex against the result table structure.
/// Both fall back gracefully to empty results if the remote is unreachable or
/// behind Cloudflare (no challenge-solver available in Rust).
use std::sync::OnceLock;

use reqwest::Client;
use sha2::{Digest, Sha256};

use crate::{
    parser,
    scrapers::{
        ScrapedUsenetStream, SearchMeta,
        prowlarr::build_series_files,
        source_health::{self, HealthGateConfig},
    },
    state::KeywordFilterCache,
};

/// Outcome of a single indexer fetch attempt, distinguishing rate-limits from
/// normal empty results so the health gate can record failures accurately.
enum IndexerOutcome {
    Success(Vec<RawUsenetItem>),
    RateLimited,
    Error,
}

const NZBINDEX_ORIGIN: &str = "https://www.nzbindex.com";
const BINSEARCH_BASE: &str = "https://www.binsearch.info";
const MOVIE_SIMILARITY_MIN: u32 = 85;
const SERIES_SIMILARITY_MIN: u32 = 80;

pub async fn scrape(
    client: &Client,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    health_gate: Option<&HealthGateConfig>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedUsenetStream> {
    let queries = build_queries(meta, media_type, season, episode);
    let mut results: Vec<ScrapedUsenetStream> = Vec::new();
    let mut seen = std::collections::HashSet::new();

    // ── NZBIndex ──────────────────────────────────────────────────────────────
    if is_allowed(health_gate, "nzbindex").await {
        let mut rate_limited = false;
        'nzb: for query in &queries {
            for page in 0..2_u32 {
                match search_nzbindex(client, query, page).await {
                    IndexerOutcome::RateLimited => {
                        rate_limited = true;
                        break 'nzb;
                    }
                    IndexerOutcome::Success(items) => {
                        for item in items {
                            if seen.insert(item.nzb_guid.clone())
                                && let Some(s) = validate_and_build(
                                    item,
                                    meta,
                                    media_type,
                                    season,
                                    episode,
                                    keyword_filters,
                                )
                            {
                                results.push(s);
                            }
                        }
                    }
                    IndexerOutcome::Error => {}
                }
            }
        }
        record_outcome(health_gate, "nzbindex", !rate_limited).await;
    } else {
        tracing::debug!("public_usenet: nzbindex health gate blocked — skipping");
    }

    // ── Binsearch ─────────────────────────────────────────────────────────────
    if is_allowed(health_gate, "binsearch").await {
        let mut rate_limited = false;
        'bin: for query in &queries {
            for page in 1..=2_u32 {
                match search_binsearch(client, query, page).await {
                    IndexerOutcome::RateLimited => {
                        rate_limited = true;
                        break 'bin;
                    }
                    IndexerOutcome::Success(items) => {
                        for item in items {
                            if seen.insert(item.nzb_guid.clone())
                                && let Some(s) = validate_and_build(
                                    item,
                                    meta,
                                    media_type,
                                    season,
                                    episode,
                                    keyword_filters,
                                )
                            {
                                results.push(s);
                            }
                        }
                    }
                    IndexerOutcome::Error => {}
                }
            }
        }
        record_outcome(health_gate, "binsearch", !rate_limited).await;
    } else {
        tracing::debug!("public_usenet: binsearch health gate blocked — skipping");
    }

    results
}

async fn is_allowed(hg: Option<&HealthGateConfig>, source: &str) -> bool {
    let Some(hg) = hg else { return true };
    if !hg.enabled {
        return true;
    }
    source_health::is_source_within_budget(
        &hg.redis,
        source,
        hg.min_samples,
        hg.min_success_rate,
        hg.max_timeout_rate,
        "general",
        &hg.scope_mode,
        &hg.scope_override,
    )
    .await
}

async fn record_outcome(hg: Option<&HealthGateConfig>, source: &str, success: bool) {
    let Some(hg) = hg else { return };
    source_health::record_source_outcome(
        &hg.redis,
        source,
        success,
        false,
        false,
        "general",
        &hg.scope_mode,
        &hg.scope_override,
        hg.counter_soft_cap,
        hg.decay_factor,
        hg.metrics_ttl_seconds,
    )
    .await;
}

// ─── Query builder ─────────────────────────────────────────────────────────────

fn build_queries(
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<String> {
    if media_type == "series" {
        match (season, episode) {
            (Some(s), Some(e)) => vec![
                format!("{} S{s:02}E{e:02}", meta.title),
                format!("{} s{s:02}e{e:02}", meta.title),
            ],
            _ => vec![meta.title.clone()],
        }
    } else {
        let mut qs = Vec::new();
        if let Some(y) = meta.year {
            qs.push(format!("{} {y}", meta.title));
        }
        qs.push(meta.title.clone());
        qs
    }
}

// ─── Shared raw item ──────────────────────────────────────────────────────────

struct RawUsenetItem {
    nzb_guid: String,
    nzb_url: String,
    name: String,
    size: i64,
    group_name: Option<String>,
    indexer: &'static str,
}

fn validate_and_build(
    item: RawUsenetItem,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Option<ScrapedUsenetStream> {
    if item.name.is_empty() {
        return None;
    }
    if keyword_filters.matches_blocked_keyword(&item.name) {
        return None;
    }
    let parsed = parser::parse_title(&item.name);
    let sim_min = if media_type == "movie" {
        MOVIE_SIMILARITY_MIN
    } else {
        SERIES_SIMILARITY_MIN
    };
    let ratio =
        parser::similarity_ratio(parsed.title.as_deref().unwrap_or(&item.name), &meta.title);
    if ratio < sim_min {
        return None;
    }
    if media_type == "movie"
        && let (Some(py), Some(my)) = (parsed.year, meta.year)
        && py != my
    {
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
    Some(ScrapedUsenetStream {
        nzb_guid: item.nzb_guid,
        nzb_url: item.nzb_url,
        name: item.name,
        size: item.size,
        indexer: item.indexer.to_string(),
        source: item.indexer.to_string(),
        group_name: item.group_name,
        parsed,
        files,
        is_cached: false,
    })
}

// ─── NZBIndex JSON API ────────────────────────────────────────────────────────

async fn search_nzbindex(client: &Client, query: &str, page: u32) -> IndexerOutcome {
    let url = format!(
        "{NZBINDEX_ORIGIN}/api/search?q={}&page={page}",
        urlencoding::encode(query)
    );
    let json: serde_json::Value = match client
        .get(&url)
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => match r.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("nzbindex json parse: {e}");
                return IndexerOutcome::Error;
            }
        },
        Ok(r) if r.status() == reqwest::StatusCode::TOO_MANY_REQUESTS => {
            tracing::debug!("nzbindex HTTP {}", r.status());
            return IndexerOutcome::RateLimited;
        }
        Ok(r) => {
            tracing::debug!("nzbindex HTTP {}", r.status());
            return IndexerOutcome::Error;
        }
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "nzbindex fetch: {e}"
            );
            return IndexerOutcome::Error;
        }
    };

    let content = match json
        .get("data")
        .and_then(|d| d.get("content"))
        .and_then(|c| c.as_array())
    {
        Some(a) => a,
        None => return IndexerOutcome::Success(vec![]),
    };

    let mut items = Vec::new();
    for entry in content {
        let release_id = match entry.get("id") {
            Some(v) => {
                if let Some(s) = v.as_str() {
                    if s.is_empty() {
                        continue;
                    }
                    s.to_string()
                } else if let Some(n) = v.as_u64() {
                    n.to_string()
                } else {
                    continue;
                }
            }
            None => continue,
        };

        let name = match entry.get("name").and_then(|v| v.as_str()) {
            Some(n) if !n.is_empty() => unescape_html(n.trim()),
            _ => continue,
        };

        let size = entry
            .get("size")
            .and_then(|v| {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .unwrap_or(0);

        let group_name = match entry.get("groups") {
            Some(serde_json::Value::Array(gs)) if !gs.is_empty() => {
                let parts: Vec<&str> = gs.iter().take(3).filter_map(|g| g.as_str()).collect();
                if parts.is_empty() {
                    None
                } else {
                    Some(parts.join(", "))
                }
            }
            Some(serde_json::Value::String(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        };

        items.push(RawUsenetItem {
            nzb_guid: sha256_prefix_40(&format!("nzbindex:{release_id}")),
            nzb_url: format!("{NZBINDEX_ORIGIN}/api/download/{release_id}.nzb"),
            name,
            size,
            group_name,
            indexer: "NZBIndex",
        });
    }
    IndexerOutcome::Success(items)
}

// ─── Binsearch HTML ───────────────────────────────────────────────────────────

static ROW_RE: OnceLock<regex::Regex> = OnceLock::new();
static CHECKBOX_RE: OnceLock<regex::Regex> = OnceLock::new();
static TITLE_RE: OnceLock<regex::Regex> = OnceLock::new();
static SIZE_SPAN_RE: OnceLock<regex::Regex> = OnceLock::new();
static GROUP_RE: OnceLock<regex::Regex> = OnceLock::new();

fn row_re() -> &'static regex::Regex {
    ROW_RE.get_or_init(|| regex::Regex::new(r"(?s)<tr[^>]*>(.*?)</tr>").unwrap())
}
fn checkbox_re() -> &'static regex::Regex {
    CHECKBOX_RE
        .get_or_init(|| regex::Regex::new(r#"(?i)type="checkbox"[^>]*name="([^"]+)""#).unwrap())
}
fn title_re() -> &'static regex::Regex {
    TITLE_RE.get_or_init(|| regex::Regex::new(r#"href="/details/[^"]*">([^<]+)</a>"#).unwrap())
}
fn size_span_re() -> &'static regex::Regex {
    SIZE_SPAN_RE
        .get_or_init(|| regex::Regex::new(r#"class="rounded-lg"[^>]*>([^<]+)</span>"#).unwrap())
}
fn group_re() -> &'static regex::Regex {
    GROUP_RE
        .get_or_init(|| regex::Regex::new(r#"href="/search\?group=[^"]*">([^<]+)</a>"#).unwrap())
}

async fn search_binsearch(client: &Client, query: &str, page: u32) -> IndexerOutcome {
    let url = format!(
        "{BINSEARCH_BASE}/search?q={}&page={page}",
        urlencoding::encode(query)
    );
    let html = match client
        .get(&url)
        // Use a browser-like UA to reduce the chance of an immediate block.
        .header("User-Agent", "Mozilla/5.0 (compatible; MediaFusion/6.0)")
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => match r.text().await {
            Ok(t) => t,
            Err(e) => {
                tracing::debug!("binsearch body: {e}");
                return IndexerOutcome::Error;
            }
        },
        Ok(r) if r.status() == reqwest::StatusCode::TOO_MANY_REQUESTS => {
            tracing::debug!("binsearch HTTP {}", r.status());
            return IndexerOutcome::RateLimited;
        }
        Ok(r) => {
            tracing::debug!("binsearch HTTP {}", r.status());
            return IndexerOutcome::Error;
        }
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "binsearch fetch: {e}"
            );
            return IndexerOutcome::Error;
        }
    };
    IndexerOutcome::Success(parse_binsearch_html(&html))
}

fn parse_binsearch_html(html: &str) -> Vec<RawUsenetItem> {
    let mut items = Vec::new();
    for cap in row_re().captures_iter(html) {
        let row = &cap[1];
        // Skip header rows (no checkbox = no result row).
        let guid = match checkbox_re().captures(row) {
            Some(c) => c[1].to_string(),
            None => continue,
        };
        let title = match title_re().captures(row) {
            Some(c) => unescape_html(c[1].trim()),
            None => continue,
        };
        if title.is_empty() {
            continue;
        }
        let size = size_span_re()
            .captures(row)
            .and_then(|c| parse_size_bytes(c[1].trim()))
            .unwrap_or(0);
        let group_name = group_re().captures(row).map(|c| unescape_html(c[1].trim()));

        items.push(RawUsenetItem {
            nzb_guid: sha256_prefix_40(&format!("binsearch:{guid}")),
            nzb_url: format!(
                "{BINSEARCH_BASE}/nzb?name={}&id={}",
                urlencoding::encode(&title),
                urlencoding::encode(&guid),
            ),
            name: title,
            size,
            group_name,
            indexer: "Binsearch",
        });
    }
    items
}

// ─── Utilities ────────────────────────────────────────────────────────────────

fn sha256_prefix_40(input: &str) -> String {
    let hash = Sha256::digest(input.as_bytes());
    hash.iter().map(|b| format!("{b:02x}")).collect::<String>()[..40].to_string()
}

fn unescape_html(s: &str) -> String {
    s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
}

static SIZE_RE: OnceLock<regex::Regex> = OnceLock::new();

fn size_re() -> &'static regex::Regex {
    SIZE_RE.get_or_init(|| regex::Regex::new(r"(?i)(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB|B)").unwrap())
}

fn parse_size_bytes(s: &str) -> Option<i64> {
    let m = size_re().captures(s)?;
    let val: f64 = m[1].parse().ok()?;
    let mult: f64 = match m[2].to_uppercase().as_str() {
        "TB" => 1_099_511_627_776.0,
        "GB" => 1_073_741_824.0,
        "MB" => 1_048_576.0,
        "KB" => 1_024.0,
        "B" => 1.0,
        _ => return None,
    };
    Some((val * mult) as i64)
}

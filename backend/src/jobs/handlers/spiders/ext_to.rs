/// Scrapers for ext.to torrent site — 5 catalog variants.
///
/// ext.to is behind Cloudflare protection. We use the byparr (FlareSolverr)
/// endpoint to get past the challenge when configured.
///
/// Scraping flow:
///   1. Browse the site (profile pages or search queries) via the listing URL.
///      HTML structure:
///        - Table rows: `table.table-striped.table-hover tbody tr`
///        - Title (browse):  `a.torrent-title-link b`
///        - Title (profile): `td.text-left .float-left a b`
///        - Size:     `td.nowrap-td .add-block-wrapper` (label "Size")
///        - Seeders:  `td .add-block-wrapper span.text-success`
///   2. For each torrent row, follow the detail link.
///      On the detail page:
///        - `window.pageToken` and `window.csrfToken` are extracted via regex.
///        - A numeric torrent ID is read from `.download-btn-magnet[data-id]`.
///        - POST `/ajax/getTorrentMagnet.php` with HMAC-SHA256 signature to get magnet.
///        - Fallback: legacy inline `magnet:?...` in HTML.
///   3. Build `ScrapedStream` and write via `persist::write_back`.
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use hmac::{Hmac, KeyInit, Mac};
use regex::Regex;
use scraper::{Html, Selector};
use sha2::Sha256;
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{
        fetcher::{fetch_byparr, fetch_plain},
        persist, ScrapedStream, SearchMeta,
    },
    util::{rate_limit, retry},
};

type HmacSha256 = Hmac<Sha256>;

// ─── Regex for security tokens ────────────────────────────────────────────────

fn page_token_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"window\.pageToken\s*=\s*(?:\\'|')([a-f0-9]{32})(?:\\'|')")
            .expect("pageToken regex")
    })
}

fn csrf_token_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"window\.csrfToken\s*=\s*(?:\\'|')([a-f0-9]{32})(?:\\'|')")
            .expect("csrfToken regex")
    })
}

fn magnet_inline_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r#"magnet:\?[^\s"'<>]+"#).expect("magnet inline regex"))
}

// ─── Config ───────────────────────────────────────────────────────────────────

/// ext.to domain (from scraper config or default).
fn ext_to_domain() -> String {
    let config_path = std::env::var("SCRAPER_CONFIG_PATH")
        .unwrap_or_else(|_| "config/scraper_config.yaml".into());
    if let Ok(text) = std::fs::read_to_string(&config_path) {
        if let Ok(root) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(domains) = root
                .get("start_urls")
                .and_then(|v| v.get("ext_to"))
                .and_then(|v| v.as_array())
            {
                if let Some(domain) = domains.first().and_then(|v| v.as_str()) {
                    return domain.to_string();
                }
            }
        }
    }
    "ext.to".to_string()
}

// ─── Catalog definition ───────────────────────────────────────────────────────

pub(crate) struct CatalogSpec {
    /// Human-readable source label stored on each stream row.
    source: &'static str,
    /// Upload profile usernames to scrape.
    profiles: &'static [&'static str],
    /// Search queries to scrape.
    queries: &'static [&'static str],
    /// Keyword pattern (plain substring, case-insensitive) for filtering.
    keyword: &'static str,
    /// Media type to use when persisting ("movie" or "series").
    media_type: &'static str,
}

const FORMULA_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/Formula",
    profiles: &["egortech", "f1carreras", "smcgill1969"],
    queries: &["formula 1", "formula 2", "formula 3"],
    keyword: "formula",
    media_type: "movie",
};

const MOTOGP_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/MotoGP",
    profiles: &["smcgill1969"],
    queries: &["motogp"],
    keyword: "motogp",
    media_type: "movie",
};

const WWE_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/WWE",
    profiles: &[],
    queries: &["wwe"],
    keyword: "wwe",
    media_type: "movie",
};

const UFC_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/UFC",
    profiles: &[],
    queries: &["ufc"],
    keyword: "ufc",
    media_type: "movie",
};

const MOVIES_TV_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/MoviesTv",
    profiles: &[],
    queries: &["movies 2026", "movies 2025", "series 2026", "series 2025"],
    keyword: "",
    media_type: "movie",
};

// ─── HMAC helpers ─────────────────────────────────────────────────────────────

/// Compute HMAC-SHA256(`torrent_id|ts|page_token`) as a lowercase hex string.
fn compute_hmac(torrent_id: u64, ts: u64, page_token: &str) -> String {
    let message = format!("{torrent_id}|{ts}|{page_token}");
    let mut mac =
        HmacSha256::new_from_slice(page_token.as_bytes()).expect("HMAC key length is valid");
    mac.update(message.as_bytes());
    let result = mac.finalize().into_bytes();
    result.iter().map(|b| format!("{b:02x}")).collect()
}

fn unix_ts() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ─── HTML parsing helpers ─────────────────────────────────────────────────────

fn extract_security_tokens(html: &str) -> (Option<String>, Option<String>) {
    let page_token = page_token_re()
        .captures(html)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_lowercase());
    let csrf = csrf_token_re()
        .captures(html)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_lowercase());
    (page_token, csrf)
}

fn extract_inline_magnet(html: &str) -> Option<String> {
    magnet_inline_re()
        .find(html)
        .map(|m| m.as_str().replace("&amp;", "&"))
}

// ─── Core scraping logic ──────────────────────────────────────────────────────

async fn fetch_html(
    label: &str,
    url: &str,
    client: &reqwest::Client,
    byparr_url: &Option<String>,
) -> Option<String> {
    retry::with_retry(label, || {
        let url = url.to_string();
        let client = client.clone();
        let bp = byparr_url.clone();
        async move {
            if let Some(bp_url) = &bp {
                if let Some(r) = fetch_byparr(&client, bp_url, &url).await {
                    return Ok(r.html);
                }
            }
            fetch_plain(&client, &url)
                .await
                .map(|r| r.html)
                .ok_or_else(|| format!("fetch failed: {url}"))
        }
    })
    .await
    .ok()
}

/// Parse listing-page rows.  Returns (title, detail_url, seeders_opt, size_opt).
fn parse_listing_rows(
    html: &str,
    base_url: &str,
    is_profile_page: bool,
    keyword: &str,
) -> Vec<(String, String, Option<i32>, Option<i64>)> {
    let doc = Html::parse_document(html);

    let row_sel = Selector::parse("table.table-striped.table-hover tbody tr").expect("row_sel");
    let browse_link_sel = Selector::parse("a.torrent-title-link").expect("browse_link_sel");
    let profile_link_sel = Selector::parse("td.text-left .float-left a").expect("profile_link_sel");
    let seeders_sel =
        Selector::parse("td .add-block-wrapper span.text-success").expect("seeders_sel");
    let size_wrapper_sel =
        Selector::parse("td.nowrap-td .add-block-wrapper").expect("size_wrapper_sel");
    let add_block_sel = Selector::parse("span.add-block").expect("add_block_sel");

    let mut results = Vec::new();

    for row in doc.select(&row_sel) {
        let link_el = if is_profile_page {
            row.select(&profile_link_sel).next()
        } else {
            row.select(&browse_link_sel)
                .next()
                .or_else(|| row.select(&profile_link_sel).next())
        };

        let Some(link) = link_el else { continue };
        let name_parts: String = link
            .select(&Selector::parse("b").unwrap())
            .flat_map(|b| b.text())
            .collect();
        let title = name_parts.trim().to_string();
        if title.is_empty() {
            continue;
        }
        if !keyword.is_empty() && !title.to_lowercase().contains(keyword) {
            continue;
        }

        let Some(href) = link.value().attr("href") else {
            continue;
        };
        let detail_url = if href.starts_with("http") {
            href.to_string()
        } else {
            format!("{base_url}{href}")
        };

        let seeders: Option<i32> = row
            .select(&seeders_sel)
            .next()
            .and_then(|el| el.text().next())
            .and_then(|t| t.parse().ok());

        let mut size_bytes: Option<i64> = None;
        for wrapper in row.select(&size_wrapper_sel) {
            let label = wrapper
                .select(&add_block_sel)
                .next()
                .map(|s| s.text().collect::<String>());
            if label.as_deref().map(|l| l.to_lowercase().contains("size")) == Some(true) {
                let size_text: String = wrapper
                    .text()
                    .filter(|t| !t.contains("Size"))
                    .collect::<Vec<_>>()
                    .join("")
                    .trim()
                    .to_string();
                size_bytes = parse_size_to_bytes(&size_text);
                break;
            }
        }

        results.push((title, detail_url, seeders, size_bytes));
    }
    results
}

/// Very simple human-readable size parser: "1.23 GB" → bytes.
fn parse_size_to_bytes(s: &str) -> Option<i64> {
    let s = s.trim().to_lowercase();
    let (num_str, unit) = s.split_once(' ')?;
    let num: f64 = num_str.parse().ok()?;
    let multiplier: f64 = match unit.trim() {
        "b" => 1.0,
        "kb" | "kib" => 1024.0,
        "mb" | "mib" => 1024.0 * 1024.0,
        "gb" | "gib" => 1024.0 * 1024.0 * 1024.0,
        "tb" | "tib" => 1024.0 * 1024.0 * 1024.0 * 1024.0,
        _ => return None,
    };
    Some((num * multiplier) as i64)
}

/// Fetch a detail page and extract the magnet link.
async fn fetch_magnet(
    label: &str,
    base_url: &str,
    detail_url: &str,
    client: &reqwest::Client,
    byparr_url: &Option<String>,
) -> Option<String> {
    let detail_html = fetch_html(label, detail_url, client, byparr_url).await?;

    // Try AJAX magnet endpoint first.
    let (page_token, csrf) = extract_security_tokens(&detail_html);
    if let (Some(pt), Some(_csrf)) = (page_token, csrf) {
        // Extract numeric torrent ID — do this in a scoped block so that the
        // non-Send `Html` value is dropped before any `.await` point.
        let numeric_id: Option<u64> = {
            let doc = Html::parse_document(&detail_html);
            let btn_sel = Selector::parse(".download-btn-magnet").expect("download-btn-magnet");
            let from_attr: Option<u64> = doc
                .select(&btn_sel)
                .next()
                .and_then(|el| el.value().attr("data-id"))
                .and_then(|s| s.parse().ok());
            // doc is dropped here (end of block).
            from_attr
        }
        // Also fall back to extracting from the URL itself (last `-<id>` segment).
        .or_else(|| {
            detail_url
                .rsplit('-')
                .next()
                .and_then(|s| s.trim_end_matches('/').parse().ok())
        });

        if let Some(tid) = numeric_id {
            let ts = unix_ts();
            let sig = compute_hmac(tid, ts, &pt);
            let ajax_url = format!("{base_url}/ajax/getTorrentMagnet.php");

            let body = serde_json::json!({
                "torrent_id": tid,
                "ts": ts,
                "token": sig,
            });

            if let Ok(magnet) = retry::with_retry(label, || {
                let client = client.clone();
                let ajax_url = ajax_url.clone();
                let body = body.clone();
                let referer = detail_url.to_string();
                async move {
                    let resp = client
                        .post(&ajax_url)
                        .header("X-Requested-With", "XMLHttpRequest")
                        .header("Referer", &referer)
                        .json(&body)
                        .timeout(std::time::Duration::from_secs(20))
                        .send()
                        .await
                        .map_err(|e| e.to_string())?
                        .json::<serde_json::Value>()
                        .await
                        .map_err(|e| e.to_string())?;

                    if resp.get("success").and_then(|v| v.as_bool()) != Some(true) {
                        return Err(format!(
                            "getTorrentMagnet failed: {}",
                            resp.get("error")
                                .and_then(|e| e.as_str())
                                .unwrap_or("unknown")
                        ));
                    }

                    let magnet = resp
                        .get("url")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .map(|s| s.to_string())
                        .or_else(|| {
                            resp.get("hash")
                                .and_then(|v| v.as_str())
                                .filter(|s| !s.is_empty())
                                .map(|h| format!("magnet:?xt=urn:btih:{h}"))
                        })
                        .ok_or_else(|| "no url/hash in response".to_string())?;
                    Ok(magnet)
                }
            })
            .await
            {
                return Some(magnet);
            }
        }
    }

    // Fallback: inline magnet in HTML.
    extract_inline_magnet(&detail_html)
}

// ─── Main catalog scraper ─────────────────────────────────────────────────────

pub(crate) async fn scrape_ext_catalog(spec: &CatalogSpec, ctx: &JobCtx) -> Result<(), JobError> {
    let domain = ext_to_domain();
    let base_url = format!("https://{domain}");
    let client = &ctx.state.http;
    let byparr_url = ctx.state.config.byparr_url.clone();
    let pool = &ctx.state.pool;
    let rate_key = domain.clone();

    let mut start_urls: Vec<(String, bool)> = Vec::new(); // (url, is_profile_page)

    for username in spec.profiles {
        start_urls.push((format!("{base_url}/user/{username}/uploads/"), true));
    }
    for query in spec.queries {
        let encoded = urlencoding::encode(query);
        start_urls.push((
            format!("{base_url}/browse/?q={encoded}&sort=seeds&order=desc"),
            false,
        ));
    }

    let mut all_streams: Vec<ScrapedStream> = Vec::new();

    for (start_url, is_profile) in start_urls {
        let mut current_url = start_url.clone();

        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            rate_limit::wait(&rate_key, 1).await;

            let html = match fetch_html(spec.source, &current_url, client, &byparr_url).await {
                Some(h) => h,
                None => {
                    warn!("{}: failed to fetch listing {current_url}", spec.source);
                    break;
                }
            };

            let rows = parse_listing_rows(&html, &base_url, is_profile, spec.keyword);

            if rows.is_empty() {
                debug!("{}: no rows on {current_url}", spec.source);
                break;
            }

            info!("{}: {} rows on {current_url}", spec.source, rows.len());

            for (title, detail_url, seeders, size) in rows {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                rate_limit::wait(&rate_key, 1).await;

                let magnet =
                    fetch_magnet(spec.source, &base_url, &detail_url, client, &byparr_url).await;

                let Some(magnet) = magnet else {
                    debug!("{}: no magnet for {detail_url}", spec.source);
                    continue;
                };

                let info_hash = parser::extract_info_hash(&magnet).map(|h| h.to_lowercase());
                let Some(info_hash) = info_hash else {
                    debug!(
                        "{}: can't parse info_hash from magnet {magnet}",
                        spec.source
                    );
                    continue;
                };

                let parsed = parser::parse_title(&title);
                let stream = ScrapedStream {
                    info_hash,
                    name: title,
                    source: spec.source.to_string(),
                    seeders,
                    size,
                    parsed,
                    files: vec![],
                    is_cached: false,
                };
                all_streams.push(stream);
            }

            // Pagination.
            let next_url = find_next_page_url(&html, &base_url, &current_url, is_profile);
            match next_url {
                Some(next) => current_url = next,
                None => break,
            }
        }
    }

    if !all_streams.is_empty() {
        let meta = SearchMeta {
            media_id: 0,
            imdb_id: None,
            title: String::new(),
            year: None,
        };
        persist::write_back(&all_streams, pool, &meta, spec.media_type, None, None).await;
    }

    Ok(())
}

/// Find the next-page URL from the listing page HTML.
fn find_next_page_url(
    html: &str,
    base_url: &str,
    current_url: &str,
    is_profile: bool,
) -> Option<String> {
    let doc = Html::parse_document(html);

    if is_profile {
        // Profile pagination: div.pagination-block > a.page-link (>> text = next)
        let sel = Selector::parse("div.pagination-block a.page-link").ok()?;
        for link in doc.select(&sel) {
            let text: String = link.text().collect();
            if text.contains(">>") {
                let href = link.value().attr("href")?;
                return Some(if href.starts_with("http") {
                    href.to_string()
                } else {
                    format!("{base_url}{href}")
                });
            }
        }
    } else {
        // Browse pagination: ul.pagination li.active + li a
        let sel = Selector::parse("ul.pagination li.active + li a").ok()?;
        if let Some(link) = doc.select(&sel).next() {
            let href = link.value().attr("href")?;
            // Don't revisit the same URL.
            let next = if href.starts_with("http") {
                href.to_string()
            } else {
                format!("{base_url}{href}")
            };
            if next != current_url {
                return Some(next);
            }
        }
    }
    None
}

// ─── Job handlers ─────────────────────────────────────────────────────────────

pub struct FormulaExtCrawl;

#[async_trait]
impl JobHandler for FormulaExtCrawl {
    const QUEUE: &'static str = "spider_formula_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&FORMULA_SPEC, &ctx).await
    }
}

pub struct MotogpExtCrawl;

#[async_trait]
impl JobHandler for MotogpExtCrawl {
    const QUEUE: &'static str = "spider_motogp_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&MOTOGP_SPEC, &ctx).await
    }
}

pub struct WweExtCrawl;

#[async_trait]
impl JobHandler for WweExtCrawl {
    const QUEUE: &'static str = "spider_wwe_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&WWE_SPEC, &ctx).await
    }
}

pub struct UfcExtCrawl;

#[async_trait]
impl JobHandler for UfcExtCrawl {
    const QUEUE: &'static str = "spider_ufc_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&UFC_SPEC, &ctx).await
    }
}

pub struct MoviesExtCrawl;

#[async_trait]
impl JobHandler for MoviesExtCrawl {
    const QUEUE: &'static str = "spider_movies_tv_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&MOVIES_TV_SPEC, &ctx).await
    }
}

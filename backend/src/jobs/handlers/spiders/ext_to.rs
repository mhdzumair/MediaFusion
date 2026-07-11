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
///   3. Build `ScrapedStream` and write via `stream_convert::write_back_torrents`.
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
        ScrapedStream, SearchMeta, StreamFile,
        fetcher::{fetch_byparr, fetch_plain, post_byparr},
        media_resolve, stream_convert,
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
fn ext_to_domain(config_path: &str) -> String {
    if let Ok(text) = std::fs::read_to_string(config_path)
        && let Ok(root) = serde_json::from_str::<serde_json::Value>(&text)
        && let Some(domains) = root
            .get("start_urls")
            .and_then(|v| v.get("ext_to"))
            .and_then(|v| v.as_array())
        && let Some(domain) = domains.first().and_then(|v| v.as_str())
    {
        return domain.to_string();
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
    /// Sports category key (matches `catalog.name` in the DB, e.g. "formula_racing").
    category: &'static str,
}

const FORMULA_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/Formula",
    profiles: &["egortech", "f1carreras", "smcgill1969"],
    queries: &["formula 1", "formula 2", "formula 3"],
    keyword: "formula",
    media_type: "movie",
    category: "formula_racing",
};

const MOTOGP_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/MotoGP",
    profiles: &["smcgill1969"],
    queries: &["motogp"],
    keyword: "motogp",
    media_type: "movie",
    category: "motogp_racing",
};

const WWE_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/WWE",
    profiles: &[],
    queries: &["wwe"],
    keyword: "wwe",
    media_type: "movie",
    category: "fighting",
};

const UFC_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/UFC",
    profiles: &[],
    queries: &["ufc"],
    keyword: "ufc",
    media_type: "movie",
    category: "fighting",
};

const MOVIES_TV_SPEC: CatalogSpec = CatalogSpec {
    source: "ExtTo/MoviesTv",
    profiles: &[],
    queries: &["movies 2026", "movies 2025", "series 2026", "series 2025"],
    keyword: "",
    media_type: "movie",
    category: "ext_to_movie",
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
            if let Some(bp_url) = &bp
                && let Some(r) = fetch_byparr(&client, bp_url, &url).await
            {
                return Ok(r.html);
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
    // Fetch the full result (not just HTML) so we can reuse CF clearance cookies
    // in the subsequent AJAX magnet request, which is also CF-protected.
    let detail_result = retry::with_retry(label, || {
        let url = detail_url.to_string();
        let client = client.clone();
        let bp = byparr_url.clone();
        async move {
            if let Some(bp_url) = &bp
                && let Some(r) = fetch_byparr(&client, bp_url, &url).await
            {
                return Ok(r);
            }
            fetch_plain(&client, &url)
                .await
                .ok_or_else(|| format!("fetch failed: {url}"))
        }
    })
    .await
    .ok()?;
    let detail_cookies = detail_result.cookies;
    let detail_html = detail_result.html;

    // Fast path: magnet is often embedded directly in the page (e.g. inside a
    // webga.zx viewer href). Skip AJAX entirely when it's already there.
    if let Some(magnet) = extract_inline_magnet(&detail_html) {
        debug!(label, "found inline magnet, skipping AJAX");
        return Some(magnet);
    }

    // Try AJAX magnet endpoint as fallback.
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

            // Form-encoded body for byparr (FlareSolverr only supports form POST).
            let form_data = format!(
                "torrent_id={tid}&ts={ts}&token={}",
                urlencoding::encode(&sig),
            );

            // Cookie header for direct reqwest fallback.
            let cookie_header = detail_cookies
                .iter()
                .map(|(k, v)| format!("{k}={v}"))
                .collect::<Vec<_>>()
                .join("; ");

            if let Ok(magnet) = retry::with_retry(label, || {
                let client = client.clone();
                let ajax_url = ajax_url.clone();
                let body = body.clone();
                let form_data = form_data.clone();
                let referer = detail_url.to_string();
                let byparr_url = byparr_url.clone();
                let detail_cookies = detail_cookies.clone();
                let cookie_header = cookie_header.clone();
                async move {
                    // Use byparr with session cookies injected so CF is bypassed
                    // AND the PHP session is valid in the same request.
                    let raw = if let Some(ref bp_url) = byparr_url {
                        if let Some(r) =
                            post_byparr(&client, bp_url, &ajax_url, &form_data, &detail_cookies)
                                .await
                        {
                            r
                        } else {
                            // byparr unavailable — try direct with cookies (may hit CF)
                            let mut req = client
                                .post(&ajax_url)
                                .header("X-Requested-With", "XMLHttpRequest")
                                .header("Referer", &referer);
                            if !cookie_header.is_empty() {
                                req = req.header("Cookie", &cookie_header);
                            }
                            req.json(&body)
                                .timeout(std::time::Duration::from_secs(20))
                                .send()
                                .await
                                .map_err(|e| e.to_string())?
                                .text()
                                .await
                                .map_err(|e| e.to_string())?
                        }
                    } else {
                        let mut req = client
                            .post(&ajax_url)
                            .header("X-Requested-With", "XMLHttpRequest")
                            .header("Referer", &referer);
                        if !cookie_header.is_empty() {
                            req = req.header("Cookie", &cookie_header);
                        }
                        req.json(&body)
                            .timeout(std::time::Duration::from_secs(20))
                            .send()
                            .await
                            .map_err(|e| e.to_string())?
                            .text()
                            .await
                            .map_err(|e| e.to_string())?
                    };
                    debug!(label, ajax_response = %&raw[..raw.len().min(500)], "AJAX magnet response");
                    // byparr wraps JSON responses in <html><body><pre>…</pre></body></html>
                    let json_str = raw
                        .split("<pre>")
                        .nth(1)
                        .and_then(|s| s.split("</pre>").next())
                        .unwrap_or(&raw);
                    let resp = serde_json::from_str::<serde_json::Value>(json_str)
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
    let domain = ext_to_domain(&ctx.state.config.scraper_config_path);
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
            format!("{base_url}/browse/?q={encoded}&sort=added&order=desc"),
            false,
        ));
    }

    let mut total_written = 0usize;

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

                info!("{}: scraping \"{}\" — {detail_url}", spec.source, title);

                let magnet =
                    fetch_magnet(spec.source, &base_url, &detail_url, client, &byparr_url).await;

                let Some(magnet) = magnet else {
                    warn!(
                        "{}: no magnet for \"{}\" ({detail_url})",
                        spec.source, title
                    );
                    continue;
                };

                let info_hash = parser::extract_info_hash(&magnet).map(|h| h.to_lowercase());
                let Some(info_hash) = info_hash else {
                    warn!(
                        "{}: can't parse info_hash from magnet — title=\"{}\" magnet={magnet}",
                        spec.source, title
                    );
                    continue;
                };

                // Always parse technical metadata via sports-aware PTT.
                // For generic movie/TV catalog, fall back to standard parsing
                // for non-sports titles.
                let parsed = if spec.category != "ext_to_movie" || parser::is_sports_title(&title) {
                    parser::parse_sports_title(&title)
                } else {
                    parser::parse_title(&title)
                };

                // Determine whether this is a weekly series episode (Raw,
                // SmackDown, NXT, …), a race weekend (grouped into a series
                // with one episode per session), or a standalone movie (PPV
                // events, etc.).
                let wwe_info = parser::classify_wwe_title(&title);
                let racing_info = parser::parse_racing_title(&title);
                let (clean_title, year, effective_media_type, files) =
                    if let Some(ref info) = wwe_info {
                        // Weekly show → series episode.
                        // Use the episode full title as the file name so
                        // the episode appears with a descriptive label.
                        let episode_title = parser::clean_sports_title(&title);
                        let files = vec![StreamFile {
                            file_index: 0,
                            filename: episode_title,
                            season_number: info.season_number,
                            episode_number: info.episode_number,
                        }];
                        // The series itself has no year (it spans many seasons).
                        (info.series_title.to_string(), None, "series", files)
                    } else if let Some(ref racing) = racing_info
                        && let Some((episode, episode_title)) = parser::racing_session_episode(
                            racing.session.as_deref().unwrap_or(&title),
                        )
                    {
                        // Race weekend → series, one episode per session
                        // (FP1/FP2/FP3/Qualifying/Sprint/Race), ordered by
                        // `racing_session_episode`'s slot number.
                        let files = vec![StreamFile {
                            file_index: 0,
                            filename: episode_title,
                            season_number: 1,
                            episode_number: episode,
                        }];
                        (racing.series_title.clone(), racing.year, "series", files)
                    } else {
                        // Movie or PPV event.
                        let clean = parsed.title.clone().unwrap_or_else(|| title.clone());
                        (clean, parsed.year, spec.media_type, vec![])
                    };

                info!(
                    "{}: ✓ title=\"{}\" info_hash={} seeders={:?} size={:?} \
                     clean_title=\"{}\" year={:?} media_type={}",
                    spec.source,
                    title,
                    info_hash,
                    seeders,
                    size,
                    clean_title,
                    year,
                    effective_media_type,
                );

                let stub_media_type = effective_media_type.to_uppercase();
                let media_id = media_resolve::find_or_create_sports_stub(
                    pool,
                    &clean_title,
                    year,
                    None,
                    &stub_media_type,
                    spec.category,
                )
                .await
                .unwrap_or(0);

                if media_id > 0 {
                    media_resolve::link_to_catalogs(pool, media_id, &[spec.category]).await;
                }

                let stream = ScrapedStream {
                    info_hash,
                    name: title,
                    source: spec.source.to_string(),
                    seeders,
                    size,
                    parsed,
                    files,
                    is_cached: false,
                    torrent_type: crate::db::TorrentType::Public,
                    torrent_file: None,
                    announce_list: vec![],
                };

                let meta = SearchMeta {
                    media_id: crate::db::MediaId(media_id),
                    imdb_id: None,
                    title: clean_title,
                    year,
                };
                stream_convert::write_back_torrents(
                    pool,
                    &[stream],
                    &meta,
                    effective_media_type,
                    None,
                    None,
                )
                .await;
                total_written += 1;
            }

            // Pagination.
            let next_url = find_next_page_url(&html, &base_url, &current_url, is_profile);
            match next_url {
                Some(next) => current_url = next,
                None => break,
            }
        }
    }

    info!("{}: {} streams written total", spec.source, total_written);

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

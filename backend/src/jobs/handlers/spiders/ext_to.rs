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
///        - POST `/ajax/getTorrentMagnet.php` with fields `torrent_id`,
///          `download_type`, `timestamp`, `hmac` (SHA256 of
///          `torrent_id|timestamp|pageToken`, despite the field's name it's a
///          plain hash, not an HMAC) and `sessid` (the csrfToken value).
///        - This endpoint re-validates the CF clearance cookie against the
///          User-Agent that earned it, so a bare `reqwest` POST always gets
///          re-challenged even with the right cookies. We replay the cookie
///          jar + UA harvested from byparr's detail-page fetch through a
///          real browserless Chrome instance (`BROWSERLESS_URL`), which
///          passes because it's an actual browser, not a bare HTTP client.
///        - Fallback: legacy inline `magnet:?...` in HTML.
///   3. Build `ScrapedStream` and write via `stream_convert::write_back_torrents`.
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use regex::Regex;
use scraper::{Html, Selector};
use sha2::{Digest, Sha256};
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{
        ScrapedStream, SearchMeta, StreamFile, browser,
        fetcher::{fetch_byparr, fetch_plain},
        media_resolve, stream_convert,
    },
    util::{rate_limit, retry},
};

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

/// Compute SHA256(`torrent_id|ts|page_token`) as a lowercase hex string,
/// matching the site's `computeHMAC()` JS helper (a plain hash, not HMAC,
/// despite the name — the page token is mixed into the hashed message
/// rather than used as an HMAC key).
fn compute_hmac(torrent_id: u64, ts: u64, page_token: &str) -> String {
    let message = format!("{torrent_id}|{ts}|{page_token}");
    let mut hasher = Sha256::new();
    hasher.update(message.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
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

/// Parse listing-page rows.  Returns (title, detail_url, seeders_opt, size_opt, row_uploader).
fn parse_listing_rows(
    html: &str,
    base_url: &str,
    is_profile_page: bool,
    keyword: &str,
) -> Vec<(String, String, Option<i32>, Option<i64>, Option<String>)> {
    let doc = Html::parse_document(html);

    let row_sel = Selector::parse("table.table-striped.table-hover tbody tr").expect("row_sel");
    let browse_link_sel = Selector::parse("a.torrent-title-link").expect("browse_link_sel");
    let profile_link_sel = Selector::parse("td.text-left .float-left a").expect("profile_link_sel");
    let user_link_sel = Selector::parse(r#"a[href*="/user/"]"#).expect("user_link_sel");
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

        let row_uploader = row
            .select(&user_link_sel)
            .next()
            .and_then(|el| el.value().attr("href"))
            .and_then(username_from_user_href);

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

        results.push((title, detail_url, seeders, size_bytes, row_uploader));
    }
    results
}

fn username_from_user_href(href: &str) -> Option<String> {
    static USER_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = USER_RE.get_or_init(|| {
        Regex::new(r"/user/([^/?#]+)/?").expect("ext.to user href regex")
    });
    re.captures(href)
        .and_then(|caps| caps.get(1))
        .map(|m| m.as_str().to_string())
}

fn extract_uploader_from_detail_html(html: &str) -> Option<String> {
    let doc = Html::parse_document(html);
    let selectors = [
        r#"a.simple-user[href*="/user/"]"#,
        r#".detail-torrent-poster-info a[href*="/user/"]"#,
        r#"a[href*="/user/"]"#,
    ];
    for sel in selectors {
        let Ok(parsed) = Selector::parse(sel) else {
            continue;
        };
        if let Some(href) = doc
            .select(&parsed)
            .next()
            .and_then(|el| el.value().attr("href"))
            .and_then(username_from_user_href)
        {
            return Some(href);
        }
    }
    None
}

fn infer_uploader_from_title(title: &str, category: &str) -> Option<String> {
    if category != "formula_racing" {
        return None;
    }

    static ALIAS_RE: std::sync::OnceLock<Vec<(Regex, &'static str)>> = std::sync::OnceLock::new();
    let aliases = ALIAS_RE.get_or_init(|| {
        [
            ("egortech", "egortech"),
            ("f1carreras", "F1Carreras"),
            ("smcgill1969", "smcgill1969"),
        ]
        .into_iter()
        .map(|(alias, canonical)| {
            let pattern = format!(
                r"(?i)(?:^|[.\-_\[\]\s]){}(?:$|[.\-_\[\]\s])",
                regex::escape(alias)
            );
            (Regex::new(&pattern).expect("formula uploader alias regex"), canonical)
        })
        .collect()
    });

    for (re, canonical) in aliases {
        if re.is_match(title) {
            return Some((*canonical).to_string());
        }
    }
    None
}

fn resolve_uploader(
    page_uploader: Option<&str>,
    row_uploader: Option<String>,
    detail_uploader: Option<String>,
    title: &str,
    category: &str,
) -> Option<String> {
    let inferred = infer_uploader_from_title(title, category);
    let detected = page_uploader
        .map(str::to_string)
        .or(row_uploader)
        .or(detail_uploader);

    if let (Some(inf), Some(det)) = (&inferred, &detected) {
        if inf.to_lowercase() != det.to_lowercase() {
            return inferred;
        }
    }
    detected.or(inferred)
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

/// Fetch a detail page and extract the magnet link plus uploader (when present).
async fn fetch_magnet(
    label: &str,
    base_url: &str,
    detail_url: &str,
    client: &reqwest::Client,
    byparr_url: &Option<String>,
    browserless_url: &Option<String>,
) -> Option<(String, Option<String>)> {
    // Fetch the full result (not just HTML) so we can reuse the CF clearance
    // cookies (and the User-Agent that earned them) in the subsequent AJAX
    // magnet request, which is also CF-protected.
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
    let detail_user_agent = detail_result.user_agent;
    let detail_html = detail_result.html;
    let detail_uploader = extract_uploader_from_detail_html(&detail_html);

    // Fast path: magnet is often embedded directly in the page (e.g. inside a
    // webga.zx viewer href). Skip AJAX entirely when it's already there.
    if let Some(magnet) = extract_inline_magnet(&detail_html) {
        debug!(label, "found inline magnet, skipping AJAX");
        return Some((magnet, detail_uploader));
    }

    // Try AJAX magnet endpoint as fallback.
    let (page_token, csrf) = extract_security_tokens(&detail_html);
    if let (Some(pt), Some(csrf)) = (page_token, csrf) {
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
            let ajax_url = format!("{base_url}/ajax/getTorrentMagnet.php");

            // Cloudflare re-validates the clearance cookie against the
            // User-Agent that earned it, so a bare reqwest POST with the
            // right cookies still gets re-challenged. Replay the cookie jar
            // + UA through a real browserless Chrome instance instead —
            // that passes because it's an actual browser executing the
            // fetch(), not a bare HTTP client.
            let Some(bl_url) = browserless_url else {
                warn!(
                    "{label}: BROWSERLESS_URL not configured — ext.to's magnet AJAX \
                     endpoint requires replaying a CF-cleared cookie through a real \
                     browser, which byparr/reqwest alone cannot do."
                );
                return extract_inline_magnet(&detail_html).map(|magnet| (magnet, detail_uploader.clone()));
            };

            if let Ok(magnet) = retry::with_retry(label, || {
                let client = client.clone();
                let ajax_url = ajax_url.clone();
                let referer = detail_url.to_string();
                let bl_url = bl_url.clone();
                let detail_cookies = detail_cookies.clone();
                let detail_user_agent = detail_user_agent.clone();
                let pt = pt.clone();
                let csrf = csrf.clone();
                async move {
                    let ts = unix_ts();
                    let sig = compute_hmac(tid, ts, &pt);
                    let form_data = format!(
                        "torrent_id={tid}&download_type=magnet&timestamp={ts}&hmac={}&sessid={}",
                        urlencoding::encode(&sig),
                        urlencoding::encode(&csrf),
                    );

                    let raw = browser::post_with_cookies_via_browser(
                        &client,
                        &bl_url,
                        &referer,
                        &ajax_url,
                        &form_data,
                        &detail_cookies,
                        &detail_user_agent,
                    )
                    .await
                    .ok_or_else(|| "browserless request failed".to_string())?;

                    debug!(label, ajax_response = %&raw[..raw.len().min(500)], "AJAX magnet response");
                    let resp = serde_json::from_str::<serde_json::Value>(&raw)
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
                return Some((magnet, detail_uploader));
            }
        }
    }

    // Fallback: inline magnet in HTML.
    extract_inline_magnet(&detail_html).map(|magnet| (magnet, detail_uploader))
}

// ─── DHT file-list enrichment for race-weekend torrents ──────────────────────

/// Whether `info_hash` already has more than one resolved `stream_file` row
/// (i.e. a previous scrape or play-time resolution already mapped the real
/// multi-session file list) — used to skip the DHT round-trip on repeat scrapes.
async fn racing_files_already_resolved(pool: &sqlx::PgPool, info_hash: &str) -> bool {
    sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM stream_file sf \
         JOIN torrent_stream ts ON ts.stream_id = sf.stream_id \
         WHERE ts.info_hash = $1",
    )
    .bind(info_hash)
    .fetch_one(pool)
    .await
    .map(|c| c > 1)
    .unwrap_or(false)
}

/// Race-weekend torrents are frequently bundled as a single multi-file
/// torrent covering every session (FP1/FP2/FP3/Qualifying/Sprint/Race), but
/// the title alone only tells us which *one* session this particular release
/// is about. ext.to exposes no direct `.torrent` download link (only a
/// magnet/hash AJAX endpoint), so to discover the real file list — and map
/// every session bundled inside to its correct episode — we resolve the
/// magnet's info-dict via DHT (BEP-9) instead of guessing a single
/// `file_index: 0` stub.
///
/// Best-effort: DHT resolution can fail for freshly-seeded torrents with no
/// peers yet, or simply time out. On any failure we fall back to `fallback`
/// (the single session parsed from the title), so behavior never regresses.
async fn resolve_racing_files_via_dht(
    info_hash: &str,
    proxy_url: Option<&str>,
    fallback: Vec<StreamFile>,
) -> Vec<StreamFile> {
    let meta =
        match crate::demagnetize::resolve(info_hash, std::time::Duration::from_secs(20), proxy_url)
            .await
        {
            Ok(m) => m,
            Err(e) => {
                debug!("ext_to: demagnetize failed for {info_hash}: {e}");
                return fallback;
            }
        };

    let mut files: Vec<StreamFile> = meta
        .files
        .iter()
        .enumerate()
        .filter_map(|(idx, f)| {
            let base = std::path::Path::new(&f.path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(&f.path);
            if !parser::episode_detector::is_video_file(base) {
                return None;
            }
            let (episode, _) = parser::racing_file_episode(base)?;
            Some(StreamFile {
                file_index: idx as i32,
                filename: base.to_string(),
                season_number: 1,
                episode_number: episode,
            })
        })
        .collect();

    if files.is_empty() {
        debug!("ext_to: no recognisable sessions in DHT file list for {info_hash}");
        return fallback;
    }
    files.sort_by_key(|f| f.episode_number);
    info!(
        "ext_to: DHT-resolved {} session file(s) for {info_hash}",
        files.len()
    );
    files
}

// ─── Main catalog scraper ─────────────────────────────────────────────────────

pub(crate) async fn scrape_ext_catalog(spec: &CatalogSpec, ctx: &JobCtx) -> Result<(), JobError> {
    let domain = ext_to_domain(&ctx.state.config.scraper_config_path);
    let base_url = format!("https://{domain}");
    let client = &ctx.state.http;
    let byparr_url = ctx.state.config.byparr_url.clone();
    let browserless_url = ctx.state.config.browserless_url.clone();
    let pool = &ctx.state.pool;
    let rate_key = domain.clone();

    let mut start_urls: Vec<(String, bool, Option<&str>)> = Vec::new(); // (url, is_profile_page, profile_username)

    for username in spec.profiles {
        start_urls.push((
            format!("{base_url}/user/{username}/uploads/"),
            true,
            Some(username),
        ));
    }
    for query in spec.queries {
        let encoded = urlencoding::encode(query);
        start_urls.push((
            format!("{base_url}/browse/?q={encoded}&sort=added&order=desc"),
            false,
            None,
        ));
    }

    let mut total_written = 0usize;

    for (start_url, is_profile, page_uploader) in start_urls {
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

            for (title, detail_url, seeders, size, row_uploader) in rows {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                rate_limit::wait(&rate_key, 1).await;

                info!("{}: scraping \"{}\" — {detail_url}", spec.source, title);

                let magnet_result = fetch_magnet(
                    spec.source,
                    &base_url,
                    &detail_url,
                    client,
                    &byparr_url,
                    &browserless_url,
                )
                .await;

                let Some((magnet, detail_uploader)) = magnet_result else {
                    warn!(
                        "{}: no magnet for \"{}\" ({detail_url})",
                        spec.source, title
                    );
                    continue;
                };

                let uploader = resolve_uploader(
                    page_uploader,
                    row_uploader,
                    detail_uploader,
                    &title,
                    spec.category,
                );

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
                let (clean_title, year, effective_media_type, files) = if let Some(ref info) =
                    wwe_info
                {
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
                    && let Some((episode, episode_title)) =
                        parser::racing_session_episode(racing.session.as_deref().unwrap_or(&title))
                {
                    // Race weekend → series, one episode per session
                    // (FP1/FP2/FP3/Qualifying/Sprint/Race), ordered by
                    // `racing_session_episode`'s slot number. The torrent
                    // itself may bundle every session as separate files —
                    // resolve the real file list via DHT so all of them get
                    // mapped, not just the session named in this release's title.
                    //
                    // `file_index: -1` marks this as an *unverified* guess (we
                    // don't actually know this session's position in the real
                    // torrent). Playback file-selection treats negative
                    // indices as absent, so it won't lock onto the wrong file
                    // the way a confident-but-wrong `0` would.
                    let fallback = vec![StreamFile {
                        file_index: -1,
                        filename: episode_title,
                        season_number: 1,
                        episode_number: episode,
                    }];
                    let files = if racing_files_already_resolved(pool, &info_hash).await {
                        fallback
                    } else {
                        resolve_racing_files_via_dht(
                            &info_hash,
                            ctx.state.config.requests_proxy_url.as_deref(),
                            fallback,
                        )
                        .await
                    };
                    (racing.series_title.clone(), racing.year, "series", files)
                } else {
                    // Movie or PPV event.
                    let clean = parsed.title.clone().unwrap_or_else(|| title.clone());
                    (clean, parsed.year, spec.media_type, vec![])
                };

                info!(
                    "{}: ✓ title=\"{}\" info_hash={} seeders={:?} size={:?} uploader={:?} \
                     clean_title=\"{}\" year={:?} media_type={}",
                    spec.source,
                    title,
                    info_hash,
                    seeders,
                    size,
                    uploader,
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
                    // Sports stub series have no external metadata provider, so
                    // `episode`/`season` rows never get created on their own —
                    // without this, `file_media_link` points at the right
                    // season/episode but the Stremio "videos" list stays empty.
                    // Register every file (not just the first) so a DHT-resolved
                    // multi-session torrent shows all its sessions, not just one.
                    for f in &files {
                        let _ = crate::db::upsert_series_episode(
                            pool,
                            crate::db::MediaId(media_id),
                            f.season_number,
                            f.episode_number,
                            &f.filename,
                        )
                        .await;
                    }
                }

                // `store_torrent_stream` skips `link_torrent_to_media` entirely when
                // the info_hash already exists in the DB (e.g. this torrent was
                // already scraped once with just a single-session stub) — capture
                // the DHT-resolved multi-session file list now so we can persist it
                // via the idempotent by-hash upsert path below regardless of that.
                let extra_files_to_persist = (files.len() > 1).then(|| files.clone());
                let info_hash_for_upsert = info_hash.clone();

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
                    uploader,
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

                if let Some(extra_files) = extra_files_to_persist {
                    let entries: Vec<crate::db::streams::TorrentFileEntry> = extra_files
                        .iter()
                        .map(|f| crate::db::streams::TorrentFileEntry {
                            file_index: f.file_index,
                            filename: f.filename.clone(),
                            size: 0,
                            season: Some(f.season_number),
                            episode: Some(f.episode_number),
                        })
                        .collect();
                    let _ = crate::db::streams::upsert_stream_files(
                        pool,
                        &info_hash_for_upsert,
                        &entries,
                    )
                    .await;
                }
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

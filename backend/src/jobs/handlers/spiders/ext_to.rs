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
///        - Parse the `#torrent_files` table when present (egortech uploads
///          expose the real multi-session file list here — no DHT needed).
///        - `window.pageToken` and `window.csrfToken` are extracted via regex.
///        - A numeric torrent ID is read from `.download-btn-magnet[data-id]`
///          (or `.download-btn-torrent[data-id]` when only a file is offered).
///        - POST `/ajax/getTorrentMagnet.php` with fields `torrent_id`,
///          `download_type` (`torrent` preferred when a file download exists,
///          otherwise `magnet`), `timestamp`, `hmac` (SHA256 of
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
        media_resolve, stream_convert, torrent_metadata,
    },
    util::{rate_limit, retry},
};

use super::formula_racing::{self, HtmlTorrentFile};
use super::spider_args::{effective_page_limit, parse_listing_page_args};

type ListingRow = (String, String, Option<i32>, Option<i64>, Option<String>);

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
) -> Vec<ListingRow> {
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
    let re =
        USER_RE.get_or_init(|| Regex::new(r"/user/([^/?#]+)/?").expect("ext.to user href regex"));
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
            (
                Regex::new(&pattern).expect("formula uploader alias regex"),
                canonical,
            )
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

    if let (Some(inf), Some(det)) = (&inferred, &detected)
        && inf.to_lowercase() != det.to_lowercase()
    {
        return inferred;
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

/// Parsed detail-page payload: magnet link, optional `.torrent` bytes, and any
/// file rows the site exposes directly in HTML.
struct DetailDownload {
    magnet: String,
    torrent_bytes: Option<Vec<u8>>,
    html_files: Vec<HtmlTorrentFile>,
    uploader: Option<String>,
}

/// Parse `#torrent_files` rows from a detail page (Python `parse_torrent_details`).
fn parse_html_file_list(html: &str) -> Vec<HtmlTorrentFile> {
    let doc = Html::parse_document(html);
    let row_sel = Selector::parse("#torrent_files table tr").expect("torrent_files row");
    let name_sel =
        Selector::parse("td.file-name-line-td span.folder-name a").expect("torrent file name");
    let size_sel = Selector::parse("td.file-size-td div.file-size").expect("torrent file size");

    let mut files = Vec::new();
    for row in doc.select(&row_sel) {
        let Some(name_el) = row.select(&name_sel).next() else {
            continue;
        };
        let filename: String = name_el.text().collect();
        let filename = filename.trim().to_string();
        if filename.is_empty() || !parser::episode_detector::is_video_file(&filename) {
            continue;
        }

        let size_texts: Vec<String> = row
            .select(&size_sel)
            .flat_map(|el| el.text())
            .map(|t| t.trim().to_string())
            .filter(|t| !t.is_empty())
            .collect();
        let size_raw = size_texts
            .get(1)
            .or_else(|| size_texts.first())
            .map(String::as_str);
        let size = size_raw.and_then(parse_size_to_bytes);

        files.push(HtmlTorrentFile {
            file_index: files.len() as i32,
            filename,
            size,
        });
    }
    files
}

fn detail_has_torrent_download(html: &str) -> bool {
    let doc = Html::parse_document(html);
    let torrent_btn = Selector::parse(".download-btn-torrent").expect("download-btn-torrent");
    doc.select(&torrent_btn).next().is_some()
}

async fn post_ext_to_ajax(
    label: &str,
    client: &reqwest::Client,
    browserless_url: &str,
    detail_url: &str,
    ajax_url: &str,
    tid: u64,
    page_token: &str,
    csrf: &str,
    download_type: &str,
    detail_cookies: &[(String, String)],
    detail_user_agent: &str,
) -> Option<String> {
    retry::with_retry(label, || {
        let client = client.clone();
        let ajax_url = ajax_url.to_string();
        let referer = detail_url.to_string();
        let bl_url = browserless_url.to_string();
        let detail_cookies = detail_cookies.to_vec();
        let detail_user_agent = detail_user_agent.to_string();
        let pt = page_token.to_string();
        let csrf = csrf.to_string();
        let download_type = download_type.to_string();
        async move {
            let ts = unix_ts();
            let sig = compute_hmac(tid, ts, &pt);
            let form_data = format!(
                "torrent_id={tid}&download_type={}&timestamp={ts}&hmac={}&sessid={}",
                urlencoding::encode(&download_type),
                urlencoding::encode(&sig),
                urlencoding::encode(&csrf),
            );

            browser::post_with_cookies_via_browser(
                &client,
                &bl_url,
                &referer,
                &ajax_url,
                &form_data,
                &detail_cookies,
                &detail_user_agent,
            )
            .await
            .ok_or_else(|| "browserless request failed".to_string())
        }
    })
    .await
    .ok()
}

fn magnet_from_ajax_response(raw: &str) -> Option<String> {
    let resp = serde_json::from_str::<serde_json::Value>(raw).ok()?;
    if resp.get("success").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    resp.get("url")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty() && s.starts_with("magnet:"))
        .map(|s| s.to_string())
        .or_else(|| {
            resp.get("hash")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|h| format!("magnet:?xt=urn:btih:{h}"))
        })
}

async fn torrent_bytes_from_ajax_response(
    client: &reqwest::Client,
    browserless_url: &str,
    base_url: &str,
    detail_url: &str,
    raw: &str,
) -> Option<Vec<u8>> {
    if raw.as_bytes().first() == Some(&b'd') {
        return Some(raw.as_bytes().to_vec());
    }

    let resp = serde_json::from_str::<serde_json::Value>(raw).ok()?;
    if resp.get("success").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }

    let url = resp.get("url").and_then(|v| v.as_str()).filter(|s| {
        !s.is_empty()
            && !s.starts_with("magnet:")
            && (s.contains(".torrent") || s.contains("/download"))
    })?;

    let absolute = if url.starts_with("http") {
        url.to_string()
    } else {
        format!("{base_url}/{}", url.trim_start_matches('/'))
    };

    browser::fetch_torrent_via_browser(client, browserless_url, detail_url, &absolute)
        .await
        .or(torrent_metadata::download_torrent_bytes(
            client,
            &absolute,
            std::time::Duration::from_secs(30),
        )
        .await)
}

/// Fetch a detail page and extract magnet / torrent file plus HTML file rows.
async fn fetch_detail_download(
    label: &str,
    base_url: &str,
    detail_url: &str,
    client: &reqwest::Client,
    byparr_url: &Option<String>,
    browserless_url: &Option<String>,
) -> Option<DetailDownload> {
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
    let html_files = parse_html_file_list(&detail_html);

    let mut magnet = extract_inline_magnet(&detail_html);
    let mut torrent_bytes: Option<Vec<u8>> = None;

    let (page_token, csrf) = extract_security_tokens(&detail_html);
    if let (Some(pt), Some(csrf)) = (page_token.clone(), csrf.clone()) {
        let numeric_id: Option<u64> = {
            let doc = Html::parse_document(&detail_html);
            let magnet_btn = Selector::parse(".download-btn-magnet").expect("download-btn-magnet");
            let torrent_btn =
                Selector::parse(".download-btn-torrent").expect("download-btn-torrent");
            doc.select(&torrent_btn)
                .next()
                .or_else(|| doc.select(&magnet_btn).next())
                .and_then(|el| el.value().attr("data-id"))
                .and_then(|s| s.parse().ok())
        }
        .or_else(|| {
            detail_url
                .rsplit('-')
                .next()
                .and_then(|s| s.trim_end_matches('/').parse().ok())
        });

        if let Some(tid) = numeric_id {
            let ajax_url = format!("{base_url}/ajax/getTorrentMagnet.php");
            if browserless_url.is_none() {
                warn!(
                    "{label}: BROWSERLESS_URL not configured — ext.to AJAX downloads \
                     require replaying a CF-cleared cookie through a real browser."
                );
            }

            if let Some(bl_url) = browserless_url.as_deref() {
                let prefer_torrent =
                    detail_has_torrent_download(&detail_html) || !html_files.is_empty();

                if prefer_torrent
                    && let Some(raw) = post_ext_to_ajax(
                        label,
                        client,
                        bl_url,
                        detail_url,
                        &ajax_url,
                        tid,
                        &pt,
                        &csrf,
                        "torrent",
                        &detail_cookies,
                        &detail_user_agent,
                    )
                    .await
                {
                    debug!(label, ajax_response = %&raw[..raw.len().min(500)], "AJAX torrent response");
                    torrent_bytes = torrent_bytes_from_ajax_response(
                        client, bl_url, base_url, detail_url, &raw,
                    )
                    .await;
                    if torrent_bytes.is_some() {
                        debug!(label, "downloaded .torrent via AJAX");
                    }
                }

                if magnet.is_none()
                    && let Some(raw) = post_ext_to_ajax(
                        label,
                        client,
                        bl_url,
                        detail_url,
                        &ajax_url,
                        tid,
                        &pt,
                        &csrf,
                        "magnet",
                        &detail_cookies,
                        &detail_user_agent,
                    )
                    .await
                {
                    debug!(label, ajax_response = %&raw[..raw.len().min(500)], "AJAX magnet response");
                    magnet = magnet_from_ajax_response(&raw);
                }
            }
        }
    }

    let magnet = magnet?;
    Some(DetailDownload {
        magnet,
        torrent_bytes,
        html_files,
        uploader: detail_uploader,
    })
}

// ─── Main catalog scraper ─────────────────────────────────────────────────────

pub(crate) async fn scrape_ext_catalog(
    spec: &CatalogSpec,
    args: &serde_json::Value,
    ctx: &JobCtx,
) -> Result<(), JobError> {
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
    let (_, start_page) = parse_listing_page_args(args);
    let page_limit = effective_page_limit(args);

    for (start_url, is_profile, page_uploader) in start_urls {
        let mut current_url = start_url.clone();
        let mut listing_page = 1u32;
        let mut pages_scraped = 0u32;

        // Advance to `start_page` without processing rows.
        while listing_page < start_page {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            rate_limit::wait(&rate_key, 1).await;
            let Some(html) = fetch_html(spec.source, &current_url, client, &byparr_url).await
            else {
                break;
            };
            let next_url = find_next_page_url(&html, &base_url, &current_url, is_profile);
            match next_url {
                Some(next) => {
                    current_url = next;
                    listing_page += 1;
                }
                None => break,
            }
        }

        loop {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            if pages_scraped >= page_limit {
                break;
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

            info!(
                "{}: {} rows on {current_url} (listing page {listing_page})",
                spec.source,
                rows.len()
            );
            pages_scraped += 1;

            for (title, detail_url, seeders, size, row_uploader) in rows {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                rate_limit::wait(&rate_key, 1).await;

                info!("{}: scraping \"{}\" — {detail_url}", spec.source, title);

                let download = fetch_detail_download(
                    spec.source,
                    &base_url,
                    &detail_url,
                    client,
                    &byparr_url,
                    &browserless_url,
                )
                .await;

                let Some(download) = download else {
                    warn!(
                        "{}: no magnet for \"{}\" ({detail_url})",
                        spec.source, title
                    );
                    continue;
                };

                let magnet = download.magnet;
                let detail_uploader = download.uploader;
                let html_files = download.html_files;
                let torrent_bytes = download.torrent_bytes;

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
                // with one episode per session), Drive to Survive, or a
                // standalone movie (PPV events, etc.).
                let wwe_info = parser::classify_fighting_series_title(&title);
                let racing_info = parser::parse_racing_title(&title);
                let drive_to_survive = parser::classify_drive_to_survive(&title);
                let (clean_title, year, effective_media_type, files) =
                    if let Some((series_title, season, episode)) = drive_to_survive {
                        let episode_title = parsed
                            .episode_title
                            .clone()
                            .unwrap_or_else(|| parser::clean_sports_title(&title));
                        let files = vec![StreamFile {
                            file_index: 0,
                            filename: episode_title,
                            season_number: season,
                            episode_number: episode,
                        }];
                        (series_title, None, "series", files)
                    } else if let Some(ref info) = wwe_info {
                        let episode_title = parser::clean_sports_title(&title);
                        let files = vec![StreamFile {
                            file_index: 0,
                            filename: episode_title,
                            season_number: info.season_number,
                            episode_number: info.episode_number,
                        }];
                        (info.series_title.clone(), None, "series", files)
                    } else if let Some(ref racing) = racing_info {
                        let display_title = racing
                            .session
                            .clone()
                            .unwrap_or_else(|| parser::clean_sports_title(&title));
                        let files = formula_racing::resolve_racing_files(
                            spec.source,
                            &info_hash,
                            &html_files,
                            torrent_bytes.as_deref(),
                            &display_title,
                            pool,
                            ctx.state.config.requests_proxy_url.as_deref(),
                        )
                        .await;
                        (racing.series_title.clone(), racing.year, "series", files)
                    } else {
                        let clean = if spec.category == "fighting" {
                            parser::clean_fighting_event_title(&title)
                        } else {
                            parsed.title.clone().unwrap_or_else(|| title.clone())
                        };
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
                let media_id = if spec.category == "fighting" {
                    media_resolve::resolve_fighting_media(
                        pool,
                        client,
                        &clean_title,
                        year,
                        effective_media_type,
                        spec.category,
                        ctx.state.config.tmdb_api_key.as_deref(),
                        ctx.state.config.imdb_cinemeta_fallback_enabled,
                    )
                    .await
                    .unwrap_or(0)
                } else {
                    media_resolve::find_or_create_sports_stub(
                        pool,
                        &clean_title,
                        year,
                        None,
                        &stub_media_type,
                        spec.category,
                    )
                    .await
                    .unwrap_or(0)
                };

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
                Some(next) => {
                    current_url = next;
                    listing_page += 1;
                }
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

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&FORMULA_SPEC, &args, &ctx).await
    }
}

pub struct MotogpExtCrawl;

#[async_trait]
impl JobHandler for MotogpExtCrawl {
    const QUEUE: &'static str = "spider_motogp_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&MOTOGP_SPEC, &args, &ctx).await
    }
}

pub struct WweExtCrawl;

#[async_trait]
impl JobHandler for WweExtCrawl {
    const QUEUE: &'static str = "spider_wwe_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&WWE_SPEC, &args, &ctx).await
    }
}

pub struct UfcExtCrawl;

#[async_trait]
impl JobHandler for UfcExtCrawl {
    const QUEUE: &'static str = "spider_ufc_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&UFC_SPEC, &args, &ctx).await
    }
}

pub struct MoviesExtCrawl;

#[async_trait]
impl JobHandler for MoviesExtCrawl {
    const QUEUE: &'static str = "spider_movies_tv_ext";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_ext_catalog(&MOVIES_TV_SPEC, &args, &ctx).await
    }
}

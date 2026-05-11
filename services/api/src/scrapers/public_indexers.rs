/// Registry-driven public torrent indexer scraper.
///
/// Handles three handler types:
///   - Rss          — BT4G RSS feed (plain XML, no challenge)
///   - SubsPleaseJson — SubsPlease JSON search API
///   - Html         — CSS-selector HTML parsing, optional Byparr for CF-protected sites
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use reqwest::Client;
use scraper::{Html, Selector};

use crate::{
    parser,
    scrapers::{
        fetcher,
        prowlarr::build_series_files,
        public_indexer_registry::{get_indexers_for_media, HandlerType, IndexerDef},
        rss::parse_rss_xml,
        source_health::{self, HealthGateConfig},
        ScrapedStream, SearchMeta,
    },
};

const MOVIE_SIMILARITY_MIN: u32 = 85;
const SERIES_SIMILARITY_MIN: u32 = 80;

static MAGNET_RE: OnceLock<regex::Regex> = OnceLock::new();
static SIZE_RE: OnceLock<regex::Regex> = OnceLock::new();

fn magnet_re() -> &'static regex::Regex {
    MAGNET_RE.get_or_init(|| {
        regex::Regex::new(r#"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^"'<>\s]*"#).unwrap()
    })
}

fn size_re() -> &'static regex::Regex {
    SIZE_RE.get_or_init(|| regex::Regex::new(r"(?i)(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB|B)").unwrap())
}

/// Maximum wall-clock time for the entire public-indexers scrape.
/// Each per-site request has its own 10 s timeout inside fetcher.rs; this
/// budget caps the total in case many indexers time out sequentially.
const SCRAPE_BUDGET: Duration = Duration::from_secs(25);

// ─── Public entry point ───────────────────────────────────────────────────────

pub async fn scrape(
    client: &Client,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    byparr_url: Option<&str>,
    enabled_sites: Option<&str>,
    health_gate: Option<&HealthGateConfig>,
) -> Vec<ScrapedStream> {
    let byparr_available = byparr_url.is_some();
    let indexers = get_indexers_for_media(media_type, enabled_sites, byparr_available);

    let mut results: Vec<ScrapedStream> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let deadline = Instant::now() + SCRAPE_BUDGET;

    for indexer in indexers {
        if Instant::now() >= deadline {
            tracing::debug!("public_indexers: budget exhausted, stopping early");
            break;
        }

        // Health gate: skip sources that are consistently failing
        if let Some(hg) = health_gate {
            if hg.enabled {
                let within_budget = source_health::is_source_within_budget(
                    &hg.redis,
                    indexer.key,
                    hg.min_samples,
                    hg.min_success_rate,
                    hg.max_timeout_rate,
                    media_type,
                    &hg.scope_mode,
                    &hg.scope_override,
                )
                .await;
                if !within_budget {
                    let snapshot = source_health::get_source_health(
                        &hg.redis,
                        indexer.key,
                        media_type,
                        &hg.scope_mode,
                        &hg.scope_override,
                    )
                    .await;
                    // Recovery: let sources with enough consecutive successes through
                    let recovery_streak = hg.recovery_success_streak;
                    if recovery_streak > 0 && snapshot.consecutive_success >= recovery_streak {
                        tracing::debug!(
                            "public_indexers: health gate recovery admission for {}",
                            indexer.key
                        );
                    } else {
                        tracing::debug!(
                            "public_indexers: health gate blocked {} (success_rate={:.2}, timeout_rate={:.2})",
                            indexer.key,
                            snapshot.success_rate(),
                            snapshot.timeout_rate(),
                        );
                        continue;
                    }
                }
            }
        }

        let (streams, request_ok) =
            scrape_indexer(client, indexer, meta, media_type, season, episode, byparr_url).await;

        // Record outcome for health tracking
        if let Some(hg) = health_gate {
            if hg.enabled {
                source_health::record_source_outcome(
                    &hg.redis,
                    indexer.key,
                    !streams.is_empty() || request_ok,
                    false, // timed_out — not tracked at this granularity yet
                    false, // challenge_solved
                    media_type,
                    &hg.scope_mode,
                    &hg.scope_override,
                    hg.counter_soft_cap,
                    hg.decay_factor,
                    hg.metrics_ttl_seconds,
                )
                .await;
            }
        }

        for s in streams {
            if seen.insert(s.info_hash.clone()) {
                results.push(s);
            }
        }
    }

    results
}

async fn scrape_indexer(
    client: &Client,
    indexer: &'static IndexerDef,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    byparr_url: Option<&str>,
) -> (Vec<ScrapedStream>, bool) {
    match indexer.handler {
        HandlerType::Rss => scrape_rss(client, indexer, meta, media_type, season, episode).await,
        HandlerType::SubsPleaseJson => {
            scrape_subsplease(client, indexer, meta, season, episode).await
        }
        HandlerType::Html => {
            scrape_html(
                client, indexer, meta, media_type, season, episode, byparr_url,
            )
            .await
        }
    }
}

// ─── Query builder ────────────────────────────────────────────────────────────

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
                format!("{} {}x{}", meta.title, s, e),
                format!("{} S{s:02}", meta.title),
                meta.title.clone(),
            ],
            _ => vec![meta.title.clone()],
        }
    } else {
        let mut qs = Vec::new();
        if let Some(y) = meta.year {
            qs.push(format!("{} ({y})", meta.title));
            qs.push(format!("{} {y}", meta.title));
        }
        qs.push(meta.title.clone());
        qs
    }
}

// ─── RSS handler (BT4G) ───────────────────────────────────────────────────────

async fn scrape_rss(
    client: &Client,
    indexer: &IndexerDef,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> (Vec<ScrapedStream>, bool) {
    let queries = build_queries(meta, media_type, season, episode);
    let sim_min = if media_type == "movie" {
        MOVIE_SIMILARITY_MIN
    } else {
        SERIES_SIMILARITY_MIN
    };
    let mut results = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut had_http_success = false;

    for query in &queries {
        let url = indexer.query_url_templates[0].replace("{query}", &urlencoding::encode(query));
        let text = match client
            .get(&url)
            .timeout(std::time::Duration::from_secs(20))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => {
                had_http_success = true;
                match r.text().await {
                    Ok(t) => t,
                    Err(e) => {
                        tracing::debug!("{}: rss body: {e}", indexer.key);
                        continue;
                    }
                }
            }
            Ok(r) => {
                tracing::debug!("{}: rss HTTP {}", indexer.key, r.status());
                continue;
            }
            Err(e) => {
                tracing::debug!("{}: rss fetch: {e}", indexer.key);
                continue;
            }
        };

        for item in parse_rss_xml(&text) {
            let title = match item.title.as_deref().filter(|t| !t.trim().is_empty()) {
                Some(t) => t.to_string(),
                None => continue,
            };
            if parser::contains_adult_keywords(&title) {
                continue;
            }
            let link = match item.link.as_deref() {
                Some(l) => l.replace("&amp;", "&"),
                None => continue,
            };
            let info_hash = match parser::extract_info_hash(&link) {
                Some(h) => h.to_lowercase(),
                None => continue,
            };
            if !seen.insert(info_hash.clone()) {
                continue;
            }
            let parsed = parser::parse_title(&title);
            let ratio =
                parser::similarity_ratio(parsed.title.as_deref().unwrap_or(&title), &meta.title);
            if ratio < sim_min {
                continue;
            }
            if media_type == "movie" {
                if let (Some(py), Some(my)) = (parsed.year, meta.year) {
                    if py != my {
                        continue;
                    }
                }
            }
            let files = if media_type == "series" {
                build_series_files(&parsed, season, episode)
            } else {
                vec![]
            };
            if media_type == "series" && files.is_empty() {
                continue;
            }
            let size = item.description.as_deref().and_then(parse_size_bytes);
            results.push(ScrapedStream {
                info_hash,
                name: title,
                source: indexer.source_name.to_string(),
                seeders: Some(0),
                size,
                parsed,
                files,
                is_cached: false,
            });
        }
    }
    (results, had_http_success)
}

// ─── SubsPlease JSON handler ──────────────────────────────────────────────────

async fn scrape_subsplease(
    client: &Client,
    indexer: &IndexerDef,
    meta: &SearchMeta,
    season: Option<i32>,
    episode: Option<i32>,
) -> (Vec<ScrapedStream>, bool) {
    let url = indexer.query_url_templates[0].replace("{query}", &urlencoding::encode(&meta.title));

    let json: serde_json::Value = match client
        .get(&url)
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => match r.json().await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("subsplease json parse: {e}");
                return (vec![], true); // got 2xx but couldn't parse — still counts as http ok
            }
        },
        Ok(r) => {
            tracing::debug!("subsplease HTTP {}", r.status());
            return (vec![], false);
        }
        Err(e) => {
            tracing::debug!("subsplease fetch: {e}");
            return (vec![], false);
        }
    };

    let obj = match json.as_object() {
        Some(o) => o,
        None => return (vec![], false),
    };

    let mut results = Vec::new();
    for (_, show_data) in obj {
        let show_name = match show_data.get("show").and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s.to_string(),
            _ => continue,
        };
        let sim = parser::similarity_ratio(&show_name, &meta.title);
        if sim < SERIES_SIMILARITY_MIN {
            continue;
        }
        let downloads = match show_data.get("downloads").and_then(|v| v.as_array()) {
            Some(d) => d,
            None => continue,
        };
        for dl in downloads {
            let magnet = match dl
                .get("magnet")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
            {
                Some(m) => m,
                None => continue,
            };
            let info_hash = match parser::extract_info_hash(magnet) {
                Some(h) => h.to_lowercase(),
                None => continue,
            };
            let res = dl.get("res").and_then(|v| v.as_str()).unwrap_or("");
            let name = if res.is_empty() {
                show_name.clone()
            } else {
                format!("{show_name} - {res}p")
            };
            let size = dl
                .get("size")
                .and_then(|v| v.as_str())
                .and_then(parse_size_bytes);
            let parsed = parser::parse_title(&name);
            let files = build_series_files(&parsed, season, episode);
            if files.is_empty() {
                continue;
            }
            results.push(ScrapedStream {
                info_hash,
                name,
                source: indexer.source_name.to_string(),
                seeders: None,
                size,
                parsed,
                files,
                is_cached: false,
            });
        }
    }
    (results, true) // got a successful HTTP response
}

// ─── HTML handler ─────────────────────────────────────────────────────────────

/// Owned data extracted synchronously from a single listing row.
/// Held across `.await` boundaries, so must be fully `Send`.
struct RowData {
    title: String,
    magnet_href: Option<String>,
    detail_href: Option<String>,
    size_str: Option<String>,
    seeder_str: Option<String>,
}

/// Extract row data from the HTML document without any async operations.
/// The `Html` type is not `Send`; dropping it before any `.await` keeps the
/// future `Send` and allows it to be spawned onto a `JoinSet`.
fn extract_row_data(doc: &Html, indexer: &IndexerDef) -> Vec<RowData> {
    select_rows(doc, indexer.row_selectors)
        .into_iter()
        .filter_map(|row| {
            let title = select_text_in_element(row, indexer.title_selectors)?;
            if title.is_empty() {
                return None;
            }
            Some(RowData {
                title,
                magnet_href: select_text_in_element(row, indexer.magnet_selectors),
                detail_href: select_text_in_element(row, indexer.detail_selectors),
                size_str: select_text_in_element(row, indexer.size_selectors),
                seeder_str: select_text_in_element(row, indexer.seeder_selectors),
            })
        })
        .collect()
}

async fn scrape_html(
    client: &Client,
    indexer: &'static IndexerDef,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    byparr_url: Option<&str>,
) -> (Vec<ScrapedStream>, bool) {
    let queries = build_queries(meta, media_type, season, episode);
    let sim_min = if media_type == "movie" {
        MOVIE_SIMILARITY_MIN
    } else {
        SERIES_SIMILARITY_MIN
    };
    let mut results = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut had_http_success = false;

    'outer: for query in &queries {
        let encoded = urlencoding::encode(query).to_string();

        'templates: for url_template in indexer.query_url_templates {
            for page in 1..=indexer.pages_per_query {
                let url = url_template
                    .replace("{query}", &encoded)
                    .replace("{page}", &page.to_string());

                let fr = match fetcher::fetch_for_indexer(
                    client,
                    byparr_url,
                    &url,
                    indexer.solve_cloudflare,
                    indexer.http_fallback,
                )
                .await
                {
                    Some(r) => {
                        had_http_success = true;
                        r
                    }
                    None => {
                        tracing::debug!("{}: fetch failed for {url}", indexer.key);
                        continue 'templates;
                    }
                };

                let base_url = fr.final_url.clone();

                // Parse HTML and extract all row data synchronously so the non-Send
                // `Html` document is dropped before any subsequent `.await`.
                let row_data: Vec<RowData> = {
                    let doc = Html::parse_document(&fr.html);
                    let data = extract_row_data(&doc, indexer);
                    if data.is_empty() {
                        tracing::debug!("{}: no rows found on {url}", indexer.key);
                    }
                    data
                }; // `doc` dropped here — future is now Send again

                for data in row_data {
                    if results.len() >= 50 {
                        break 'outer;
                    }
                    if let Some(stream) = process_row_data(
                        client, indexer, meta, media_type, season, episode, data, &base_url,
                        sim_min, byparr_url,
                    )
                    .await
                    {
                        if seen.insert(stream.info_hash.clone()) {
                            results.push(stream);
                        }
                    }
                }
                if !results.is_empty() {
                    break 'templates;
                }
            }
        }
    }
    (results, had_http_success)
}

#[allow(clippy::too_many_arguments)]
async fn process_row_data(
    client: &Client,
    indexer: &IndexerDef,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    data: RowData,
    base_url: &str,
    sim_min: u32,
    byparr_url: Option<&str>,
) -> Option<ScrapedStream> {
    if parser::contains_adult_keywords(&data.title) {
        return None;
    }
    let parsed = parser::parse_title(&data.title);
    let ratio =
        parser::similarity_ratio(parsed.title.as_deref().unwrap_or(&data.title), &meta.title);
    if ratio < sim_min {
        return None;
    }
    if media_type == "movie" {
        if let (Some(py), Some(my)) = (parsed.year, meta.year) {
            if py != my {
                return None;
            }
        }
    }

    // Try direct magnet from listing row
    let direct_hash = data
        .magnet_href
        .as_deref()
        .filter(|m| m.starts_with("magnet:"))
        .and_then(parser::extract_info_hash)
        .map(|h| h.to_lowercase());

    let info_hash = match direct_hash {
        Some(h) => h,
        None => {
            // No direct magnet — follow detail page link
            let detail_href = data.detail_href?;
            if detail_href.len() > indexer.max_detail_url_length {
                return None;
            }
            let detail_url = resolve_url(base_url, &detail_href)?;
            let dr = fetcher::fetch_for_indexer(
                client,
                byparr_url,
                &detail_url,
                indexer.solve_cloudflare,
                indexer.http_fallback,
            )
            .await?;
            let magnet = find_magnet_in_html(&dr.html)?;
            parser::extract_info_hash(&magnet)?.to_lowercase()
        }
    };

    let size = data.size_str.as_deref().and_then(parse_size_bytes);
    let seeders = data
        .seeder_str
        .as_deref()
        .and_then(|s| s.trim().parse::<i32>().ok());

    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };
    if media_type == "series" && files.is_empty() {
        return None;
    }

    Some(ScrapedStream {
        info_hash,
        name: data.title,
        source: indexer.source_name.to_string(),
        seeders,
        size,
        parsed,
        files,
        is_cached: false,
    })
}

fn find_magnet_in_html(html: &str) -> Option<String> {
    magnet_re().find(html).map(|m| m.as_str().to_string())
}

// ─── CSS selector helpers ─────────────────────────────────────────────────────

enum SelectorPseudo {
    Text,
    Attr(String),
    None,
}

/// Strip parsel-style `::text` / `::attr(name)` pseudo-elements from a selector
/// string, returning the clean CSS and what to extract.
fn parse_pseudo(s: &str) -> (&str, SelectorPseudo) {
    if let Some(css) = s.strip_suffix("::text") {
        return (css, SelectorPseudo::Text);
    }
    if s.ends_with(')') {
        if let Some(pos) = s.rfind("::attr(") {
            let css = &s[..pos];
            let attr = s[pos + 7..s.len() - 1].to_string();
            return (css, SelectorPseudo::Attr(attr));
        }
    }
    (s, SelectorPseudo::None)
}

/// Try each selector in order and return the first non-empty value from `element`.
fn select_text_in_element(element: scraper::ElementRef<'_>, selectors: &[&str]) -> Option<String> {
    for selector_str in selectors {
        let (css, pseudo) = parse_pseudo(selector_str);
        let sel = match Selector::parse(css) {
            Ok(s) => s,
            Err(_) => continue,
        };
        for sub_el in element.select(&sel) {
            let value: String = match &pseudo {
                SelectorPseudo::Text | SelectorPseudo::None => {
                    sub_el.text().collect::<Vec<_>>().concat()
                }
                SelectorPseudo::Attr(attr) => match sub_el.value().attr(attr.as_str()) {
                    Some(v) => v.to_string(),
                    None => continue,
                },
            };
            let value = value.trim().to_string();
            if !value.is_empty() {
                return Some(value);
            }
        }
    }
    None
}

/// Find all rows matching the first selector that yields any elements.
fn select_rows<'a>(doc: &'a Html, selectors: &[&str]) -> Vec<scraper::ElementRef<'a>> {
    for selector_str in selectors {
        if let Ok(sel) = Selector::parse(selector_str) {
            let rows: Vec<_> = doc.select(&sel).collect();
            if !rows.is_empty() {
                return rows;
            }
        }
    }
    vec![]
}

/// Resolve a potentially-relative href against a base URL.
fn resolve_url(base: &str, href: &str) -> Option<String> {
    if href.is_empty() {
        return None;
    }
    if href.starts_with("magnet:") || href.starts_with("http://") || href.starts_with("https://") {
        return Some(href.to_string());
    }
    if href.starts_with("//") {
        let scheme = if base.starts_with("https") {
            "https"
        } else {
            "http"
        };
        return Some(format!("{scheme}:{href}"));
    }
    // Extract scheme://host from base
    let origin = base
        .find("://")
        .and_then(|i| base[i + 3..].find('/').map(|j| &base[..i + 3 + j]))
        .unwrap_or_else(|| base.trim_end_matches('/'));
    if href.starts_with('/') {
        Some(format!("{origin}{href}"))
    } else {
        let base_dir = base.rfind('/').map(|i| &base[..i]).unwrap_or(base);
        Some(format!("{base_dir}/{href}"))
    }
}

// ─── Size parsing ─────────────────────────────────────────────────────────────

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

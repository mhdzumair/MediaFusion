/// Scrapers for Tamil torrent forums: TamilMV and TamilBlasters.
///
/// Both sites use IPS (Invision Power Suite) forum software.
/// The forum listing pages expose topic links via `li[data-rowid] a[data-ipshover-target]`.
/// Each topic page then has `.torrent` download links via `a[data-fileext='torrent']`.
///
/// Since both spiders share identical structure (only the homepage differs), a
/// single shared helper `scrape_tamil_forum` does the actual work.
use async_trait::async_trait;
use scraper::{Html, Selector};
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{
        fetcher::{fetch_byparr, fetch_plain},
        media_resolve, persist, ScrapedStream, SearchMeta,
    },
    util::{rate_limit, retry},
};

// ─── IPS forum CSS selectors ──────────────────────────────────────────────────
//
// Forum listing page (e.g. /index.php?/forums/forum/<id>/page/<n>/):
//   - Topic rows:    li[data-rowid]
//   - Topic link:    a[data-ipshover-target]   (href = full topic URL)
//
// Topic / movie page:
//   - Torrent links: a[data-fileext='torrent'] (href = direct .torrent download URL)
//   - Poster image:  div[data-role='commentContent'] img (first non-GIF)

const MAX_PAGES: u32 = 5;

// ─── Config reader ────────────────────────────────────────────────────────────

/// Load the homepage for a spider from the scraper config JSON.
///
/// Falls back to a hard-coded default when the config file is missing or the
/// key is absent, so the handler can still run.
fn spider_homepage(spider_name: &str, default: &str) -> String {
    let config_path = std::env::var("SCRAPER_CONFIG_PATH")
        .unwrap_or_else(|_| "config/scraper_config.yaml".into());

    // Try to read and parse as JSON.
    if let Ok(text) = std::fs::read_to_string(&config_path) {
        if let Ok(root) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(hp) = root
                .get(spider_name)
                .and_then(|v| v.get("homepage"))
                .and_then(|v| v.as_str())
            {
                return hp.to_string();
            }
        }
    }
    default.to_string()
}

// ─── Shared scraping logic ────────────────────────────────────────────────────

/// Scrape up to `MAX_PAGES` forum listing pages for `spider_name`.
///
/// For each topic found, fetches the topic page, collects torrent download
/// links, resolves/creates a media entry via title metadata lookup, and
/// persists each stream linked to its media_id.
async fn scrape_tamil_forum(
    spider_name: &str,
    source_label: &str,
    homepage: &str,
    catalogs: &serde_json::Value,
    ctx: &JobCtx,
) -> Result<(), JobError> {
    let client = &ctx.state.http;
    let byparr_url = ctx.state.config.byparr_url.clone();
    let pool = &ctx.state.pool;

    // Build the CSS selectors once.
    let row_sel = Selector::parse("li[data-rowid]").expect("li[data-rowid]");
    let link_sel = Selector::parse("a[data-ipshover-target]").expect("a[data-ipshover-target]");
    let torrent_sel =
        Selector::parse("a[data-fileext='torrent']").expect("a[data-fileext='torrent']");

    // Collect all forum IDs from the config.
    let mut forum_ids: Vec<String> = Vec::new();
    if let Some(catalogs_map) = catalogs.as_object() {
        for (_lang, lang_val) in catalogs_map {
            if let Some(lang_obj) = lang_val.as_object() {
                for (_vtype, id_val) in lang_obj {
                    match id_val {
                        serde_json::Value::String(s) => forum_ids.push(s.clone()),
                        serde_json::Value::Array(arr) => {
                            for v in arr {
                                if let Some(s) = v.as_str() {
                                    forum_ids.push(s.to_string());
                                }
                            }
                        }
                        serde_json::Value::Number(n) => {
                            forum_ids.push(n.to_string());
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    if forum_ids.is_empty() {
        warn!("{spider_name}: no forum IDs found in config, aborting");
        return Ok(());
    }

    let mut all_streams: Vec<()> = Vec::new();

    for forum_id in &forum_ids {
        for page in 1..=MAX_PAGES {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let listing_url = format!("{homepage}/index.php?/forums/forum/{forum_id}/page/{page}/");
            rate_limit::wait(&rate_limit::domain_key(homepage), 1).await;

            let html = retry::with_retry(spider_name, || {
                let url = listing_url.clone();
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
            .unwrap_or_default();

            if html.is_empty() {
                debug!("{spider_name}: empty listing page {page} for forum {forum_id}");
                break;
            }

            // Parse the listing HTML in a scoped block so the non-Send
            // `Html` value is dropped before any `.await` point below.
            let topic_links: Vec<String> = {
                let doc = Html::parse_document(&html);
                doc.select(&row_sel)
                    .filter_map(|row| {
                        row.select(&link_sel)
                            .next()
                            .and_then(|a| a.value().attr("href"))
                            .map(|href| {
                                if href.starts_with("http") {
                                    href.to_string()
                                } else {
                                    format!("{homepage}{href}")
                                }
                            })
                    })
                    .collect()
            }; // doc dropped here

            if topic_links.is_empty() {
                debug!("{spider_name}: no topics on page {page} for forum {forum_id}");
                break;
            }

            info!(
                "{spider_name}: forum {forum_id} page {page}: {} topics",
                topic_links.len()
            );

            for topic_url in topic_links {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                rate_limit::wait(&rate_limit::domain_key(homepage), 1).await;

                let topic_html = retry::with_retry(spider_name, || {
                    let url = topic_url.clone();
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
                .unwrap_or_default();

                if topic_html.is_empty() {
                    continue;
                }

                // Extract title + torrent links from topic page in a scoped
                // block so the non-Send `Html` value is dropped before any
                // subsequent `.await` point.
                let (title, torrent_links): (String, Vec<String>) = {
                    let topic_doc = Html::parse_document(&topic_html);
                    let h1_sel = Selector::parse("h1").unwrap();
                    let title_sel = Selector::parse("title").unwrap();
                    let title = topic_doc
                        .select(&h1_sel)
                        .next()
                        .map(|el| el.text().collect::<String>().trim().to_string())
                        .filter(|s| !s.is_empty())
                        .or_else(|| {
                            topic_doc
                                .select(&title_sel)
                                .next()
                                .map(|el| el.text().collect::<String>().trim().to_string())
                        })
                        .unwrap_or_default();

                    let torrent_links: Vec<String> = topic_doc
                        .select(&torrent_sel)
                        .filter_map(|a| a.value().attr("href"))
                        .map(|href| {
                            if href.starts_with("http") {
                                href.to_string()
                            } else {
                                format!("{homepage}{href}")
                            }
                        })
                        .collect();

                    (title, torrent_links)
                    // topic_doc dropped here
                };

                if torrent_links.is_empty() || title.is_empty() {
                    continue;
                }

                // Parse the title once for this topic; derive media type from PTT.
                let parsed = parser::parse_title(&title);
                let clean_title = parsed.title.as_deref().unwrap_or(&title).to_string();
                let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();
                let media_type_str = if is_series { "SERIES" } else { "MOVIE" };

                // Resolve or create media (PTT clean title → DB lookup → TMDB → stub).
                let tmdb_key = ctx.state.config.tmdb_api_key.as_deref();
                let media_entry = media_resolve::find_or_create_media(
                    pool,
                    &ctx.state.http,
                    &clean_title,
                    parsed.year,
                    is_series,
                    &[],
                    tmdb_key,
                )
                .await;

                let (media_id, imdb_id) = match media_entry {
                    Some(m) => (m.id, None::<String>),
                    None => {
                        warn!("{spider_name}: could not find/create media for '{title}'");
                        continue;
                    }
                };

                let meta = SearchMeta {
                    media_id: media_id as i64,
                    imdb_id,
                    title: clean_title.clone(),
                    year: parsed.year,
                };

                // Each torrent link for this topic becomes one ScrapedStream.
                for torrent_url in &torrent_links {
                    rate_limit::wait(&rate_limit::domain_key(homepage), 1).await;
                    let torrent_bytes = retry::with_retry(spider_name, || {
                        let url = torrent_url.clone();
                        let client = client.clone();
                        async move {
                            client
                                .get(&url)
                                .header("User-Agent", "Mozilla/5.0")
                                .timeout(std::time::Duration::from_secs(30))
                                .send()
                                .await
                                .map_err(|e| e.to_string())?
                                .bytes()
                                .await
                                .map_err(|e| e.to_string())
                        }
                    })
                    .await
                    .unwrap_or_default();

                    let info_hash = extract_info_hash_from_torrent(&torrent_bytes);
                    let Some(info_hash) = info_hash else {
                        debug!("{spider_name}: no info_hash for {torrent_url}");
                        continue;
                    };

                    let stream = ScrapedStream {
                        info_hash,
                        name: title.clone(),
                        source: source_label.to_string(),
                        seeders: None,
                        size: None,
                        parsed: parsed.clone(),
                        files: vec![],
                        is_cached: false,
                    };
                    persist::write_back(&[stream], pool, &meta, media_type_str, None, None).await;
                    all_streams.push(());
                }
            }

            if !all_streams.is_empty() {
                info!(
                    "{spider_name}: forum {forum_id} page {page}: flushed {} streams",
                    all_streams.len()
                );
                all_streams.clear();
            }
        }
    }

    Ok(())
}

// ─── Torrent info_hash extraction ─────────────────────────────────────────────

/// Parse a bencoded `.torrent` file and return the hex-encoded SHA-1 info_hash.
///
/// This is a minimal hand-rolled bencode walker — we only need the `info`
/// dictionary offset and length, then SHA-1 hash that slice.
fn extract_info_hash_from_torrent(data: &[u8]) -> Option<String> {
    use sha1::{Digest, Sha1};

    // Find "4:info" in the bencode stream.
    let needle = b"4:info";
    let pos = data.windows(needle.len()).position(|w| w == needle)?;
    let info_start = pos + needle.len();

    // The info dict starts at info_start and extends to the matching 'e'.
    // Walk the bencode to find the end of the dict.
    let info_end = bencode_end(data, info_start)?;
    let info_slice = &data[info_start..info_end];

    let mut hasher = Sha1::new();
    hasher.update(info_slice);
    let hash = hasher.finalize();
    Some(hash.iter().map(|b| format!("{b:02x}")).collect())
}

/// Return the exclusive end index of the bencode value starting at `pos`.
fn bencode_end(data: &[u8], pos: usize) -> Option<usize> {
    if pos >= data.len() {
        return None;
    }
    match data[pos] {
        b'd' | b'l' => {
            // Dict or list: walk contents until matching 'e'.
            let mut i = pos + 1;
            loop {
                if i >= data.len() {
                    return None;
                }
                if data[i] == b'e' {
                    return Some(i + 1);
                }
                i = bencode_end(data, i)?;
            }
        }
        b'i' => {
            // Integer: i<digits>e
            let e = data[pos..].iter().position(|&b| b == b'e')?;
            Some(pos + e + 1)
        }
        b'0'..=b'9' => {
            // String: <len>:<data>
            let colon = data[pos..].iter().position(|&b| b == b':')?;
            let len_str = std::str::from_utf8(&data[pos..pos + colon]).ok()?;
            let len: usize = len_str.parse().ok()?;
            Some(pos + colon + 1 + len)
        }
        _ => None,
    }
}

// ─── Config loader ────────────────────────────────────────────────────────────

fn load_catalogs(spider_name: &str) -> serde_json::Value {
    let config_path = std::env::var("SCRAPER_CONFIG_PATH")
        .unwrap_or_else(|_| "config/scraper_config.yaml".into());

    if let Ok(text) = std::fs::read_to_string(&config_path) {
        if let Ok(root) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(catalogs) = root.get(spider_name).and_then(|v| v.get("catalogs")) {
                return catalogs.clone();
            }
        }
    }
    serde_json::Value::Object(serde_json::Map::new())
}

// ─── Job handlers ─────────────────────────────────────────────────────────────

pub struct TamilMvCrawl;

#[async_trait]
impl JobHandler for TamilMvCrawl {
    const QUEUE: &'static str = "spider_tamilmv";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let homepage = spider_homepage("tamilmv", "https://www.1tamilmv.earth");
        let catalogs = load_catalogs("tamilmv");
        scrape_tamil_forum("tamilmv", "TamilMV", &homepage, &catalogs, &ctx).await
    }
}

pub struct TamilBlastersCrawl;

#[async_trait]
impl JobHandler for TamilBlastersCrawl {
    const QUEUE: &'static str = "spider_tamil_blasters";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let homepage = spider_homepage("tamil_blasters", "https://1tamilblasters.wtf");
        let catalogs = load_catalogs("tamil_blasters");
        scrape_tamil_forum(
            "tamil_blasters",
            "TamilBlasters",
            &homepage,
            &catalogs,
            &ctx,
        )
        .await
    }
}

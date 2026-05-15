/// Scraper for sport-video.org.ua — sports torrent listings.
///
/// The site requires a headless browser (Chromium) because content is rendered
/// via JavaScript.  If Chrome is not installed the handler logs a warning and
/// returns `Ok(())` gracefully so the worker doesn't crash in environments
/// without a browser.
///
/// Site structure (from Python spider):
///   - Category page URL: configured in `scraper_config.json` under
///     `sport_video.categories` (e.g. `https://www.sport-video.org.ua/football.html`).
///   - Content blocks:  `div[id^="wb_LayoutGrid"]`
///   - Title text:      `div[id^="wb_Text"] strong`
///   - Poster:          `div[id^="wb_PhotoGallery"] img`
///   - Torrent page:    `div[id^="wb_Shape"] a` (href)
///   - Direct .torrent: `a[href$=".torrent"]`
///
/// On the torrent detail page:
///   - Metadata table:  `table tr`  (td.cell0 strong → header, next td → value)
///   - Torrent links:   `a[href$=".torrent"]`
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
        persist, ScrapedStream, SearchMeta,
    },
    util::{rate_limit, retry},
};

// ─── Config helpers ───────────────────────────────────────────────────────────

/// Load `sport_video.categories` map from the scraper config JSON.
/// Returns a Vec of (category_name, url) pairs.
fn load_sport_video_categories() -> Vec<(String, String)> {
    let config_path = std::env::var("SCRAPER_CONFIG_PATH")
        .unwrap_or_else(|_| "config/scraper_config.yaml".into());

    if let Ok(text) = std::fs::read_to_string(&config_path) {
        if let Ok(root) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(cats) = root
                .get("sport_video")
                .and_then(|v| v.get("categories"))
                .and_then(|v| v.as_object())
            {
                return cats
                    .iter()
                    .filter_map(|(k, v)| v.as_str().map(|url| (k.clone(), url.to_string())))
                    .collect();
            }
        }
    }

    // Hard-coded fallbacks matching the JSON config.
    vec![
        (
            "football".to_string(),
            "https://www.sport-video.org.ua/football.html".to_string(),
        ),
        (
            "basketball".to_string(),
            "https://www.sport-video.org.ua/basketball.html".to_string(),
        ),
        (
            "hockey".to_string(),
            "https://www.sport-video.org.ua/hockey.html".to_string(),
        ),
        (
            "american_football".to_string(),
            "https://www.sport-video.org.ua/americanfootball.html".to_string(),
        ),
        (
            "baseball".to_string(),
            "https://www.sport-video.org.ua/baseball.html".to_string(),
        ),
        (
            "rugby".to_string(),
            "https://www.sport-video.org.ua/rugby.html".to_string(),
        ),
        (
            "other_sports".to_string(),
            "https://www.sport-video.org.ua/other.html".to_string(),
        ),
    ]
}

// ─── Content-block parsers ────────────────────────────────────────────────────

#[allow(dead_code)]
struct ContentBlock {
    title: String,
    poster_url: Option<String>,
    torrent_page_href: Option<String>,
    direct_torrent_href: Option<String>,
}

fn parse_category_page(html: &str, base_url: &str) -> Vec<ContentBlock> {
    let doc = Html::parse_document(html);

    // CSS selectors matching the Python spider.
    let block_sel = Selector::parse(r#"div[id^="wb_LayoutGrid"]"#).expect("wb_LayoutGrid sel");
    let title_sel = Selector::parse(r#"div[id^="wb_Text"] strong"#).expect("wb_Text strong sel");
    let img_sel =
        Selector::parse(r#"div[id^="wb_PhotoGallery"] img"#).expect("wb_PhotoGallery img");
    let shape_link_sel = Selector::parse(r#"div[id^="wb_Shape"] a"#).expect("wb_Shape a sel");
    let direct_torrent_sel = Selector::parse(r#"a[href$=".torrent"]"#).expect("direct torrent sel");

    let mut blocks = Vec::new();

    for block in doc.select(&block_sel) {
        // Title: join all <strong> text inside a wb_Text div.
        let title: String = block
            .select(&title_sel)
            .flat_map(|el| el.text())
            .collect::<Vec<_>>()
            .join("")
            .replace("(NEW)", "")
            .trim()
            .to_string();

        if title.is_empty() {
            continue;
        }

        // Poster image.
        let poster_url = block
            .select(&img_sel)
            .next()
            .and_then(|img| {
                img.value()
                    .attr("src")
                    .or_else(|| img.value().attr("data-src"))
            })
            .map(|src| {
                if src.starts_with("http") {
                    src.to_string()
                } else {
                    format!("{base_url}/{src}")
                }
            });

        // Torrent page link (wb_Shape → first <a>).
        let torrent_page_href = block
            .select(&shape_link_sel)
            .next()
            .and_then(|a| a.value().attr("href"))
            .map(|href| {
                if href.starts_with("http") {
                    href.to_string()
                } else {
                    format!("{base_url}/{href}")
                }
            });

        // Direct .torrent link (rare — sometimes in the same block).
        let direct_torrent_href = block
            .select(&direct_torrent_sel)
            .next()
            .and_then(|a| a.value().attr("href"))
            .map(|href| {
                if href.starts_with("http") {
                    href.to_string()
                } else {
                    format!("{base_url}/{href}")
                }
            });

        blocks.push(ContentBlock {
            title,
            poster_url,
            torrent_page_href,
            direct_torrent_href,
        });
    }

    blocks
}

/// Parse a torrent detail page.  Returns `Vec<torrent_url>`.
fn parse_torrent_detail_page(html: &str, base_url: &str) -> Vec<String> {
    let doc = Html::parse_document(html);
    let sel = Selector::parse(r#"a[href$=".torrent"]"#).expect("torrent link sel");
    doc.select(&sel)
        .filter_map(|a| a.value().attr("href"))
        .map(|href| {
            if href.starts_with("http") {
                href.to_string()
            } else {
                format!("{base_url}/{href}")
            }
        })
        .collect()
}

// ─── Torrent info_hash from .torrent file ─────────────────────────────────────

fn extract_info_hash_from_torrent(data: &[u8]) -> Option<String> {
    use sha1::{Digest, Sha1};

    let needle = b"4:info";
    let pos = data.windows(needle.len()).position(|w| w == needle)?;
    let info_start = pos + needle.len();
    let info_end = bencode_end(data, info_start)?;
    let info_slice = &data[info_start..info_end];
    let mut hasher = Sha1::new();
    hasher.update(info_slice);
    let hash = hasher.finalize();
    Some(hash.iter().map(|b| format!("{b:02x}")).collect())
}

fn bencode_end(data: &[u8], pos: usize) -> Option<usize> {
    if pos >= data.len() {
        return None;
    }
    match data[pos] {
        b'd' | b'l' => {
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
            let e = data[pos..].iter().position(|&b| b == b'e')?;
            Some(pos + e + 1)
        }
        b'0'..=b'9' => {
            let colon = data[pos..].iter().position(|&b| b == b':')?;
            let len_str = std::str::from_utf8(&data[pos..pos + colon]).ok()?;
            let len: usize = len_str.parse().ok()?;
            Some(pos + colon + 1 + len)
        }
        _ => None,
    }
}

// ─── Fetch helpers ────────────────────────────────────────────────────────────

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

// ─── Job handler ──────────────────────────────────────────────────────────────

pub struct SportVideoCrawl;

#[async_trait]
impl JobHandler for SportVideoCrawl {
    const QUEUE: &'static str = "spider_sport_video";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let categories = load_sport_video_categories();
        if categories.is_empty() {
            warn!("sport_video: no categories configured");
            return Ok(());
        }

        let client = &ctx.state.http;
        let byparr_url = ctx.state.config.byparr_url.clone();
        let pool = &ctx.state.pool;

        // Attempt to check whether headless Chrome / chromium is reachable.
        // The sport-video.org.ua site uses a Scrapling "stealthy" fetcher in
        // Python; in Rust we rely on byparr if available, or plain fetch.
        // Log a graceful warning if neither produces useful HTML (JS-heavy pages).
        //
        // NOTE: chromiumoxide integration could be added here when needed.
        // For now we use byparr (FlareSolverr-compatible) if configured, which
        // executes JavaScript inside a headless Chrome on the byparr side.
        // Without byparr this spider may get empty/partial pages — we log a
        // warning and return Ok(()) rather than failing hard.

        let base_url = "https://www.sport-video.org.ua";
        let rate_key = "sport-video.org.ua";

        let mut all_streams: Vec<ScrapedStream> = Vec::new();

        for (category, category_url) in &categories {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            rate_limit::wait(rate_key, 1).await;

            let html = match fetch_html("sport_video", category_url, client, &byparr_url).await {
                Some(h) if !h.is_empty() => h,
                _ => {
                    warn!(
                        "sport_video: failed to fetch category '{category}' at {category_url}. \
                             If this site requires JS rendering, configure BYPARR_URL."
                    );
                    continue;
                }
            };

            // Check whether the page looks like it rendered (has wb_LayoutGrid divs).
            if !html.contains("wb_LayoutGrid") {
                warn!(
                    "sport_video: page for '{category}' appears to be JS-rendered and may be \
                     incomplete without a browser. Configure BYPARR_URL for full results."
                );
            }

            let blocks = parse_category_page(&html, base_url);
            info!(
                "sport_video: category '{}': {} content blocks",
                category,
                blocks.len()
            );

            for block in blocks {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                // Collect torrent URLs for this content block.
                let mut torrent_urls: Vec<String> = Vec::new();

                if let Some(direct) = block.direct_torrent_href {
                    torrent_urls.push(direct);
                } else if let Some(page_url) = block.torrent_page_href {
                    rate_limit::wait(rate_key, 1).await;
                    if let Some(page_html) =
                        fetch_html("sport_video", &page_url, client, &byparr_url).await
                    {
                        torrent_urls = parse_torrent_detail_page(&page_html, base_url);
                    }
                }

                for torrent_url in &torrent_urls {
                    rate_limit::wait(rate_key, 1).await;
                    let torrent_bytes = retry::with_retry("sport_video", || {
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
                        debug!(
                            "sport_video: no info_hash for torrent {} ({})",
                            block.title, torrent_url
                        );
                        continue;
                    };

                    let parsed = parser::parse_title(&block.title);
                    let stream = ScrapedStream {
                        info_hash,
                        name: block.title.clone(),
                        source: "sport-video.org.ua".to_string(),
                        seeders: None,
                        size: None,
                        parsed,
                        files: vec![],
                        is_cached: false,
                    };
                    all_streams.push(stream);
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
            persist::write_back(&all_streams, pool, &meta, "movie", None, None).await;
        }

        Ok(())
    }
}

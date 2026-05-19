/// Scraper for sport-video.org.ua — sports torrent listings.
///
/// The site's torrent download endpoints are protected by the adm.tools JavaScript
/// bot challenge, which requires a real browser to solve.  This handler uses a
/// browserless v2 container (configured via `BROWSERLESS_URL`) to navigate the site
/// and execute the challenge-solving fetch inside a real Chrome page context.
///
/// Site structure:
///   - Category page URL: configured in `scraper_config.yaml` under
///     `sport_video.categories` (e.g. `https://www.sport-video.org.ua/football.html`).
///   - Content blocks:  `div[id^="wb_LayoutGrid"]`
///   - Title text:      `div[id^="wb_Text"] strong`
///   - Poster:          `div[id^="wb_PhotoGallery"] img`
///   - Torrent page:    `div[id^="wb_Shape"] a` (href)
///   - Direct .torrent: `a[href$=".torrent"]`
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
        browser,
        fetcher::{fetch_byparr, fetch_plain},
        media_resolve, persist, ScrapedStream, SearchMeta,
    },
    util::rate_limit,
};

// ─── Config helpers ───────────────────────────────────────────────────────────

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

    vec![
        (
            "football".into(),
            "https://www.sport-video.org.ua/football.html".into(),
        ),
        (
            "basketball".into(),
            "https://www.sport-video.org.ua/basketball.html".into(),
        ),
        (
            "hockey".into(),
            "https://www.sport-video.org.ua/hockey.html".into(),
        ),
        (
            "american_football".into(),
            "https://www.sport-video.org.ua/americanfootball.html".into(),
        ),
        (
            "baseball".into(),
            "https://www.sport-video.org.ua/baseball.html".into(),
        ),
        (
            "rugby".into(),
            "https://www.sport-video.org.ua/rugby.html".into(),
        ),
        (
            "other_sports".into(),
            "https://www.sport-video.org.ua/other.html".into(),
        ),
    ]
}

fn category_to_genre(category: &str) -> &'static str {
    match category {
        "football" => "Football",
        "basketball" => "Basketball",
        "hockey" => "Hockey",
        "american_football" => "American Football",
        "baseball" => "Baseball",
        "rugby" => "Rugby/AFL",
        _ => "Other Sports",
    }
}

// ─── URL helpers ─────────────────────────────────────────────────────────────

fn resolve_url(base: &str, href: &str) -> String {
    let url = if href.starts_with("http") {
        href.to_string()
    } else {
        let clean = href.trim_start_matches("./").trim_start_matches('/');
        format!("{base}/{clean}")
    };
    url.replace(' ', "%20")
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
    let block_sel = Selector::parse(r#"div[id^="wb_LayoutGrid"]"#).unwrap();
    let title_sel = Selector::parse(r#"div[id^="wb_Text"] strong"#).unwrap();
    let img_sel = Selector::parse(r#"div[id^="wb_PhotoGallery"] img"#).unwrap();
    let shape_link_sel = Selector::parse(r#"div[id^="wb_Shape"] a"#).unwrap();
    let direct_torrent_sel = Selector::parse(r#"a[href$=".torrent"]"#).unwrap();

    let mut blocks = Vec::new();

    for block in doc.select(&block_sel) {
        let raw_title: String = block
            .select(&title_sel)
            .flat_map(|el| el.text())
            .collect::<Vec<_>>()
            .join("")
            .replace("(NEW)", "");
        let title = raw_title
            .split(" / ")
            .next()
            .unwrap_or(&raw_title)
            .trim()
            .trim_end_matches("TORRENT")
            .trim()
            .to_string();

        if title.is_empty() {
            continue;
        }

        let poster_url = block
            .select(&img_sel)
            .next()
            .and_then(|img| {
                img.value()
                    .attr("src")
                    .or_else(|| img.value().attr("data-src"))
            })
            .map(|src| resolve_url(base_url, src));

        let torrent_page_href = block
            .select(&shape_link_sel)
            .next()
            .and_then(|a| a.value().attr("href"))
            .map(|href| resolve_url(base_url, href));

        let direct_torrent_href = block
            .select(&direct_torrent_sel)
            .next()
            .and_then(|a| a.value().attr("href"))
            .map(|href| resolve_url(base_url, href));

        blocks.push(ContentBlock {
            title,
            poster_url,
            torrent_page_href,
            direct_torrent_href,
        });
    }

    blocks
}

fn parse_torrent_detail_page(html: &str, base_url: &str) -> Vec<String> {
    let doc = Html::parse_document(html);
    let sel = Selector::parse(r#"a[href$=".torrent"]"#).unwrap();
    doc.select(&sel)
        .filter_map(|a| a.value().attr("href"))
        .map(|href| resolve_url(base_url, href))
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
    Some(
        hasher
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    )
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
            let len: usize = std::str::from_utf8(&data[pos..pos + colon])
                .ok()?
                .parse()
                .ok()?;
            Some(pos + colon + 1 + len)
        }
        _ => None,
    }
}

// ─── Page fetch helper ────────────────────────────────────────────────────────

use crate::scrapers::fetcher::FetchResult;

async fn fetch_page(
    label: &str,
    url: &str,
    client: &reqwest::Client,
    byparr_url: &Option<String>,
) -> Option<FetchResult> {
    for attempt in 1u32..=3 {
        let result = async {
            if let Some(bp) = byparr_url {
                if let Some(r) = fetch_byparr(client, bp, url).await {
                    return Ok(r);
                }
            }
            fetch_plain(client, url)
                .await
                .ok_or_else(|| format!("fetch failed: {url}"))
        }
        .await;

        match result {
            Ok(r) => return Some(r),
            Err(e) if attempt < 3 => {
                warn!("{label}: attempt {attempt} failed — {e}, retrying");
                tokio::time::sleep(std::time::Duration::from_secs(2u64.pow(attempt))).await;
            }
            Err(e) => warn!("{label}: all retries failed — {e}"),
        }
    }
    None
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

        let Some(ref browserless_url) = ctx.state.config.browserless_url else {
            warn!(
                "sport_video: BROWSERLESS_URL is not configured — \
                 torrent downloads require a browserless v2 container to solve \
                 the adm.tools JS challenge.  Set BROWSERLESS_URL=http://browserless:3000."
            );
            return Ok(());
        };

        let base_url = "https://www.sport-video.org.ua";
        let rate_key = "sport-video.org.ua";

        for (category, category_url) in &categories {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            rate_limit::wait(rate_key, 1).await;

            // Category HTML pages are static — plain HTTP works; byparr is a bonus.
            let page = match fetch_page("sport_video", category_url, client, &byparr_url).await {
                Some(p) if !p.html.is_empty() => p,
                _ => {
                    warn!("sport_video: failed to fetch category '{category}' at {category_url}");
                    continue;
                }
            };

            if !page.html.contains("wb_LayoutGrid") {
                warn!(
                    "sport_video: page for '{category}' contains no content blocks — \
                     may be incomplete"
                );
            }

            let blocks = parse_category_page(&page.html, base_url);
            info!(
                "sport_video: category '{}': {} content blocks",
                category,
                blocks.len()
            );

            let genre = category_to_genre(category);

            for block in blocks {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                // Collect torrent URLs for this block.
                let mut torrent_urls: Vec<String> = Vec::new();

                if let Some(direct) = block.direct_torrent_href {
                    torrent_urls.push(direct);
                } else if let Some(page_url) = block.torrent_page_href {
                    rate_limit::wait(rate_key, 1).await;
                    if let Some(detail) =
                        fetch_page("sport_video", &page_url, client, &byparr_url).await
                    {
                        torrent_urls.extend(parse_torrent_detail_page(&detail.html, base_url));
                    }
                }

                let mut block_streams: Vec<ScrapedStream> = Vec::new();

                for torrent_url in &torrent_urls {
                    if ctx.is_cancelled() {
                        return Err(JobError::Cancelled);
                    }

                    // Throttle: 15 browser downloads per minute to avoid overwhelming
                    // the browserless container and the target site.
                    rate_limit::wait_rpm(rate_key, 15).await;

                    // Use browserless v2 to solve the adm.tools JS challenge and
                    // download the torrent binary from inside a real Chrome context.
                    // The category page URL is a static HTML page (no challenge) — safe
                    // to use as the navigation target that primes the browser session.
                    let torrent_bytes = browser::fetch_torrent_via_browser(
                        client,
                        browserless_url,
                        category_url,
                        torrent_url,
                    )
                    .await
                    .unwrap_or_default();

                    let Some(info_hash) = extract_info_hash_from_torrent(&torrent_bytes) else {
                        debug!(
                            "sport_video: no info_hash for '{}' ({})",
                            block.title, torrent_url
                        );
                        continue;
                    };

                    let parsed = parser::parse_title(&block.title);
                    block_streams.push(ScrapedStream {
                        info_hash,
                        name: block.title.clone(),
                        source: "sport-video.org.ua".to_string(),
                        seeders: None,
                        size: None,
                        parsed,
                        files: vec![],
                        is_cached: false,
                    });
                }

                if block_streams.is_empty() {
                    continue;
                }

                let parsed_title = parser::parse_title(&block.title);
                let media_id = media_resolve::find_or_create_sports_stub(
                    pool,
                    &block.title,
                    parsed_title.year,
                    genre,
                    block.poster_url.as_deref(),
                )
                .await
                .unwrap_or(0);

                let meta = SearchMeta {
                    media_id: media_id as i64,
                    imdb_id: None,
                    title: block.title.clone(),
                    year: parsed_title.year,
                };
                persist::write_back(&block_streams, pool, &meta, "movie", None, None).await;
            }
        }

        Ok(())
    }
}

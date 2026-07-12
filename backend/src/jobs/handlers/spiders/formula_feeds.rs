/// Formula racing RSS spider — BT4G + Reddit feeds (formulio-style).
///
/// Runs independently of the ext.to spider so scraping continues when ext.to
/// is blocked by Cloudflare. Streams are deduped by info_hash at store time.
use async_trait::async_trait;
use regex::Regex;
use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, HeaderMap, HeaderName, HeaderValue, USER_AGENT};
use reqwest::{Client, RequestBuilder};
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{browser, rss::parse_rss_xml},
    util::rate_limit,
};

use super::sports_rss_common::{classify_sports_rss_release, persist_sports_rss_stream};

const SOURCE: &str = "FormulaFeeds";
const CATEGORY: &str = "formula_racing";

const CHROME_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

// ─── Formulio feed URLs (decoded from feed.txt base64 blobs) ─────────────────

/// egortech — Reddit profile + targeted Sky F1 BT4G searches.
/// Prefer old.reddit.com — typically less aggressive rate-limiting for RSS.
const REDDIT_EGORTECH_URLS: &[&str] = &[
    "https://old.reddit.com/user/egortech/submitted/.rss",
    "https://www.reddit.com/user/egortech/submitted/.rss",
];
const EGORTECH_BT4G_SKY_UHD: &str =
    "https://bt4gprx.com/search?q=F1...Grand.Prix.SkyUHD.2160P&page=rss";
const EGORTECH_BT4G_SKY_HD: &str =
    "https://bt4gprx.com/search?q=F1...Grand.Prix.SkyF1HD.%201080P&page=rss";

/// smcg (smcgill1969) — Formula 1 Sky SD/1080p/UHD BT4G searches.
const SMCG_BT4G_SKY_SD: &str = "https://bt4gprx.com/search?q=Formula.1...SkyF1HD.SD&page=rss";
const SMCG_BT4G_SKY_1080: &str = "https://bt4gprx.com/search?q=Formula.1...SkyF1HD.1080p&page=rss";
const SMCG_BT4G_SKY_UHD: &str = "https://bt4gprx.com/search?q=Formula.1...SkyF1UHD.4K-HLG&page=rss";

/// smcm — MotoGP TNT Sports HD BT4G search.
const SMCM_BT4G_MOTOGP: &str = "https://bt4gprx.com/search?q=MotoGP..TNTSportsHD.&page=rss";

/// ss — F1TV / MULTi BT4G + Knaben RSS mirrors.
const SS_BT4G_F1TV: &str = "https://bt4gprx.com/search?q=Formula.1.F1TV..SS&page=rss";
const SS_BT4G_MULTI: &str = "https://bt4gprx.com/search?q=Formula.1.MULTi..SS&page=rss";
const SS_KNABEN_F1TV: &str = "https://rss.knaben.org/Formula.1.F1TV..SS//20";
const SS_KNABEN_MULTI: &str = "https://rss.knaben.org/formula.1.multi..ss//20";

const REDDIT_MOTORSPORTS_URLS: &[&str] = &[
    "https://old.reddit.com/r/MotorsportsReplays/.rss",
    "https://www.reddit.com/r/MotorsportsReplays/.rss",
];

/// Minimum pause between separate Reddit feed sources (egortech vs MotorsportsReplays).
const REDDIT_FEED_COOLDOWN_SECS: u64 = 90;

/// Backoff after HTTP 429 before retrying the same URL.
const REDDIT_429_BACKOFF_SECS: u64 = 90;

/// Motorsport categories accepted from the MotorsportsReplays subreddit feed.
const MOTORSPORTS_CATEGORIES: &[&str] = &["formula_racing", "motogp_racing"];

#[derive(Clone, Copy)]
enum FeedFilter {
    /// Formula 1/2/3 keyword match (egortech, smcg, ss feeds).
    FormulaOnly,
    /// MotoGP keyword match (smcm feed).
    MotoGpOnly,
    /// Any recognised motorsport category (MotorsportsReplays subreddit).
    Motorsports,
}

#[derive(Clone, Copy)]
enum FeedKind {
    Bt4g,
    Knaben,
    Reddit,
}

fn feed_request(client: &Client, kind: FeedKind, url: &str) -> RequestBuilder {
    let mut req = client.get(url).timeout(std::time::Duration::from_secs(30));

    match kind {
        FeedKind::Bt4g | FeedKind::Knaben => {
            req = req
                .header(USER_AGENT, CHROME_UA)
                .header(
                    ACCEPT,
                    "application/rss+xml, application/xml, text/xml, */*",
                )
                .header(ACCEPT_LANGUAGE, "en-US,en;q=0.9");
        }
        FeedKind::Reddit => {
            // Mirror formulio's browser-like headers — bare reqwest UA gets 403.
            let mut headers = HeaderMap::new();
            headers.insert(USER_AGENT, HeaderValue::from_static(CHROME_UA));
            headers.insert(
                ACCEPT,
                HeaderValue::from_static(
                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                ),
            );
            headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("en-US,en;q=0.9"));
            headers.insert(
                HeaderName::from_static("cache-control"),
                HeaderValue::from_static("max-age=0"),
            );
            headers.insert(
                HeaderName::from_static("sec-ch-ua"),
                HeaderValue::from_static(
                    r#""Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120""#,
                ),
            );
            headers.insert(
                HeaderName::from_static("sec-ch-ua-mobile"),
                HeaderValue::from_static("?0"),
            );
            headers.insert(
                HeaderName::from_static("sec-ch-ua-platform"),
                HeaderValue::from_static(r#""Windows""#),
            );
            headers.insert(
                HeaderName::from_static("sec-fetch-dest"),
                HeaderValue::from_static("document"),
            );
            headers.insert(
                HeaderName::from_static("sec-fetch-mode"),
                HeaderValue::from_static("navigate"),
            );
            headers.insert(
                HeaderName::from_static("sec-fetch-site"),
                HeaderValue::from_static("none"),
            );
            headers.insert(
                HeaderName::from_static("sec-fetch-user"),
                HeaderValue::from_static("?1"),
            );
            headers.insert(
                HeaderName::from_static("upgrade-insecure-requests"),
                HeaderValue::from_static("1"),
            );
            req = req.headers(headers);
        }
    }

    req
}

/// True when a response body looks like RSS/Atom XML (not an HTML error/login page).
fn looks_like_rss_xml(body: &str) -> bool {
    let trimmed = body.trim_start();
    trimmed.starts_with("<?xml") || trimmed.starts_with("<feed") || trimmed.starts_with("<rss")
}

async fn fetch_direct(client: &Client, kind: FeedKind, url: &str) -> Result<(u16, String), String> {
    let resp = feed_request(client, kind, url)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status().as_u16();
    let body = resp.text().await.map_err(|e| e.to_string())?;
    Ok((status, body))
}

/// Reddit RSS: direct HTTP first, browserless Chrome fallback on 403/429/failure.
async fn fetch_reddit_feed_xml(
    client: &Client,
    browserless_url: Option<&str>,
    urls: &[&str],
    label: &str,
) -> Option<String> {
    // Cap Reddit to ~1 request/minute across all formula feed sources.
    rate_limit::wait_rpm("formula_feeds_reddit", 1).await;

    for (attempt, url) in urls.iter().enumerate() {
        for retry in 0..=1u32 {
            if retry > 0 {
                info!(
                    "{SOURCE}/{label}: backing off {REDDIT_429_BACKOFF_SECS}s after 429 on {url}"
                );
                tokio::time::sleep(std::time::Duration::from_secs(REDDIT_429_BACKOFF_SECS)).await;
            }

            match fetch_direct(client, FeedKind::Reddit, url).await {
                Ok((status, body)) if (200..300).contains(&status) && looks_like_rss_xml(&body) => {
                    debug!(
                        "{SOURCE}/{label}: direct fetch {status} — {} bytes from {url}",
                        body.len()
                    );
                    return Some(body);
                }
                Ok((429, _)) if retry == 0 => {
                    warn!("{SOURCE}/{label}: direct HTTP 429 for {url}");
                    continue;
                }
                Ok((status, body)) => {
                    if status == 403 || status == 429 {
                        warn!("{SOURCE}/{label}: direct HTTP {status} for {url}");
                    } else if body.trim().is_empty() {
                        warn!("{SOURCE}/{label}: direct empty body (HTTP {status}) for {url}");
                    } else if !looks_like_rss_xml(&body) {
                        warn!(
                            "{SOURCE}/{label}: direct non-RSS response (HTTP {status}) for {url}"
                        );
                    }
                }
                Err(e) => {
                    warn!("{SOURCE}/{label}: direct fetch failed for {url}: {e}");
                }
            }

            if let Some(bl_url) = browserless_url {
                info!("{SOURCE}/{label}: trying browserless for {url}");
                rate_limit::wait("formula_feeds_browserless", 1).await;
                match browser::fetch_text_via_browser(client, bl_url, url, Some(CHROME_UA)).await {
                    Some((status, body))
                        if (200..300).contains(&status) && looks_like_rss_xml(&body) =>
                    {
                        info!(
                            "{SOURCE}/{label}: browserless fetch {status} — {} bytes from {url}",
                            body.len()
                        );
                        return Some(body);
                    }
                    Some((429, _)) if retry == 0 => {
                        warn!("{SOURCE}/{label}: browserless HTTP 429 for {url}");
                        continue;
                    }
                    Some((status, body)) => {
                        warn!(
                            "{SOURCE}/{label}: browserless HTTP {status} non-RSS ({} bytes) for {url}",
                            body.len()
                        );
                    }
                    None => {
                        warn!("{SOURCE}/{label}: browserless transport error for {url}");
                    }
                }
            }

            break;
        }

        if attempt + 1 < urls.len() {
            tokio::time::sleep(std::time::Duration::from_secs(15)).await;
        }
    }

    if browserless_url.is_none() {
        warn!(
            "{SOURCE}/{label}: all Reddit URLs failed — set BROWSERLESS_URL for browser fallback"
        );
    }
    None
}

async fn fetch_feed_xml(
    client: &Client,
    browserless_url: Option<&str>,
    kind: FeedKind,
    urls: &[&str],
    label: &str,
) -> Option<String> {
    if matches!(kind, FeedKind::Reddit) {
        return fetch_reddit_feed_xml(client, browserless_url, urls, label).await;
    }

    for url in urls {
        match fetch_direct(client, kind, url).await {
            Ok((status, body)) if (200..300).contains(&status) && !body.trim().is_empty() => {
                debug!("{SOURCE}/{label}: fetched {} bytes from {url}", body.len());
                return Some(body);
            }
            Ok((status, _)) => warn!("{SOURCE}/{label}: HTTP {status} for {url}"),
            Err(e) => warn!("{SOURCE}/{label}: fetch failed for {url}: {e}"),
        }
    }
    None
}

fn item_text_blobs(item: &crate::scrapers::rss::RssItem) -> Vec<String> {
    let mut blobs = Vec::new();
    if let Some(t) = &item.description {
        blobs.push(t.clone());
    }
    if let Some(t) = &item.link {
        blobs.push(t.clone());
    }
    for v in item.extras.values() {
        blobs.push(v.clone());
    }
    blobs
}

fn formula_feed_matches(title: &str) -> bool {
    static FORMULA_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = FORMULA_RE.get_or_init(|| {
        Regex::new(r"(?i)(?:formula[ ._+]*[1234e]|\bf[123e]\b|\bf1\b)").expect("formula feed regex")
    });
    re.is_match(title)
}

fn motorsports_feed_category(title: &str) -> Option<&'static str> {
    parser::detect_sports_category(title).filter(|cat| MOTORSPORTS_CATEGORIES.contains(cat))
}

fn motogp_feed_matches(title: &str) -> bool {
    parser::detect_sports_category(title) == Some("motogp_racing")
}

fn feed_item_matches(title: &str, filter: FeedFilter) -> Option<&'static str> {
    match filter {
        FeedFilter::FormulaOnly if formula_feed_matches(title) => Some(CATEGORY),
        FeedFilter::MotoGpOnly if motogp_feed_matches(title) => Some("motogp_racing"),
        FeedFilter::Motorsports => motorsports_feed_category(title),
        _ => None,
    }
}

fn extract_magnet_from_text(text: &str) -> Option<String> {
    static MAGNET_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = MAGNET_RE.get_or_init(|| {
        Regex::new("magnet:\\?xt=urn:btih:[^\\s\"<>]+").expect("magnet extract regex")
    });
    re.find(text).map(|m| m.as_str().replace("&amp;", "&"))
}

fn magnet_from_rss_item(item: &crate::scrapers::rss::RssItem) -> Option<String> {
    if let Some(link) = item.link.as_deref().map(str::trim) {
        if link.starts_with("magnet:") {
            return Some(link.replace("&amp;", "&"));
        }
        if let Some(m) = extract_magnet_from_text(link) {
            return Some(m);
        }
    }
    if let Some(url) = item
        .enclosure_url
        .as_deref()
        .filter(|l| l.starts_with("magnet:"))
    {
        return Some(url.to_string());
    }
    for blob in item_text_blobs(item) {
        if let Some(m) = extract_magnet_from_text(&blob) {
            return Some(m);
        }
    }
    // Knaben feeds expose a bare 40-char hex info_hash in `<guid>`.
    if let Some(guid) = item.guid.as_deref().map(str::trim)
        && let Some(hash) = parser::extract_info_hash(guid)
    {
        return Some(format!("magnet:?xt=urn:btih:{hash}"));
    }
    None
}

async fn scrape_formula_feeds(ctx: &JobCtx) -> Result<(), JobError> {
    let client = &ctx.state.http;
    let pool = &ctx.state.pool;
    let proxy_url = ctx.state.config.requests_proxy_url.as_deref();
    let current_year = chrono::Utc::now().format("%Y").to_string();

    struct FeedSpec {
        label: &'static str,
        kind: FeedKind,
        urls: &'static [&'static str],
        filter: FeedFilter,
        uploader: Option<&'static str>,
    }

    let feeds: [FeedSpec; 12] = [
        // egortech BT4G (formulio egor/)
        FeedSpec {
            label: "egortech/BT4G-SkyUHD",
            kind: FeedKind::Bt4g,
            urls: &[EGORTECH_BT4G_SKY_UHD],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("egortech"),
        },
        FeedSpec {
            label: "egortech/BT4G-SkyHD",
            kind: FeedKind::Bt4g,
            urls: &[EGORTECH_BT4G_SKY_HD],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("egortech"),
        },
        // smcg (formulio smcg/ — smcgill1969 Sky uploads)
        FeedSpec {
            label: "smcg/BT4G-SD",
            kind: FeedKind::Bt4g,
            urls: &[SMCG_BT4G_SKY_SD],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("smcgill1969"),
        },
        FeedSpec {
            label: "smcg/BT4G-1080p",
            kind: FeedKind::Bt4g,
            urls: &[SMCG_BT4G_SKY_1080],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("smcgill1969"),
        },
        FeedSpec {
            label: "smcg/BT4G-UHD",
            kind: FeedKind::Bt4g,
            urls: &[SMCG_BT4G_SKY_UHD],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("smcgill1969"),
        },
        // smcm (formulio smcm/ — MotoGP)
        FeedSpec {
            label: "smcm/BT4G-MotoGP",
            kind: FeedKind::Bt4g,
            urls: &[SMCM_BT4G_MOTOGP],
            filter: FeedFilter::MotoGpOnly,
            uploader: Some("smcm"),
        },
        // ss (formulio ss/ — F1TV / MULTi)
        FeedSpec {
            label: "ss/BT4G-F1TV",
            kind: FeedKind::Bt4g,
            urls: &[SS_BT4G_F1TV],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("ss"),
        },
        FeedSpec {
            label: "ss/BT4G-MULTi",
            kind: FeedKind::Bt4g,
            urls: &[SS_BT4G_MULTI],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("ss"),
        },
        FeedSpec {
            label: "ss/Knaben-F1TV",
            kind: FeedKind::Knaben,
            urls: &[SS_KNABEN_F1TV],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("ss"),
        },
        FeedSpec {
            label: "ss/Knaben-MULTi",
            kind: FeedKind::Knaben,
            urls: &[SS_KNABEN_MULTI],
            filter: FeedFilter::FormulaOnly,
            uploader: Some("ss"),
        },
        // Reddit feeds last — rate-limited; direct HTTP then browserless fallback.
        FeedSpec {
            label: "egortech/Reddit",
            kind: FeedKind::Reddit,
            urls: REDDIT_EGORTECH_URLS,
            filter: FeedFilter::FormulaOnly,
            uploader: Some("egortech"),
        },
        FeedSpec {
            label: "Reddit/MotorsportsReplays",
            kind: FeedKind::Reddit,
            urls: REDDIT_MOTORSPORTS_URLS,
            filter: FeedFilter::Motorsports,
            uploader: Some("MotorsportsReplays"),
        },
    ];

    let browserless_url = ctx.state.config.browserless_url.as_deref();
    let mut total_written = 0usize;
    let mut reddit_feeds_fetched = 0usize;

    for feed in feeds {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        if matches!(feed.kind, FeedKind::Reddit) && reddit_feeds_fetched > 0 {
            info!(
                "{SOURCE}: waiting {REDDIT_FEED_COOLDOWN_SECS}s before next Reddit feed ({})",
                feed.label
            );
            tokio::time::sleep(std::time::Duration::from_secs(REDDIT_FEED_COOLDOWN_SECS)).await;
        }

        rate_limit::wait("formula_feeds", 2).await;

        let Some(xml) =
            fetch_feed_xml(client, browserless_url, feed.kind, feed.urls, feed.label).await
        else {
            continue;
        };

        if matches!(feed.kind, FeedKind::Reddit) {
            reddit_feeds_fetched += 1;
        }

        let items = parse_rss_xml(&xml);
        let mut feed_written = 0usize;
        let mut feed_skipped_no_magnet = 0usize;
        let mut feed_skipped_filter = 0usize;

        if matches!(feed.kind, FeedKind::Reddit) {
            info!(
                "{SOURCE}/{}: parsed {} feed entries",
                feed.label,
                items.len()
            );
        }

        for item in items {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let title = match item.title.as_deref().filter(|t| !t.trim().is_empty()) {
                Some(t) => t.trim().to_string(),
                None => continue,
            };

            let Some(catalog) = feed_item_matches(&title, feed.filter) else {
                feed_skipped_filter += 1;
                continue;
            };

            if !title.contains(&current_year) {
                feed_skipped_filter += 1;
                continue;
            }

            let Some(magnet) = magnet_from_rss_item(&item) else {
                feed_skipped_no_magnet += 1;
                debug!(
                    "{SOURCE}/{}: no magnet in RSS item \"{}\"",
                    feed.label,
                    title.chars().take(80).collect::<String>()
                );
                continue;
            };

            let Some(info_hash) = parser::extract_info_hash(&magnet).map(|h| h.to_lowercase())
            else {
                continue;
            };

            let (clean_title, year, effective_media_type, files, parsed) =
                classify_sports_rss_release(&title, &info_hash, SOURCE, pool, proxy_url).await;

            let source = format!("{SOURCE}/{}", feed.label);
            info!(
                "{source}: ✓ title=\"{title}\" info_hash={info_hash} clean_title=\"{clean_title}\" \
                 media_type={effective_media_type} catalog={catalog}"
            );

            persist_sports_rss_stream(
                pool,
                &title,
                &info_hash,
                clean_title,
                year,
                effective_media_type,
                parsed,
                files,
                source,
                None,
                item.enclosure_length,
                feed.uploader.map(str::to_string),
                catalog,
                None,
            )
            .await;

            feed_written += 1;
            total_written += 1;
        }

        if matches!(feed.kind, FeedKind::Reddit) {
            info!(
                "{SOURCE}/{}: {feed_written} written, \
                 {feed_skipped_filter} skipped (filter), \
                 {feed_skipped_no_magnet} skipped (no magnet)",
                feed.label
            );
        }
    }

    info!("{SOURCE}: {total_written} streams written from RSS feeds");
    Ok(())
}

pub struct FormulaFeedsCrawl;

#[async_trait]
impl JobHandler for FormulaFeedsCrawl {
    const QUEUE: &'static str = "spider_formula_feeds";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_formula_feeds(&ctx).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn looks_like_rss_xml_accepts_atom_and_rss() {
        assert!(looks_like_rss_xml(
            r#"<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>"#
        ));
        assert!(looks_like_rss_xml(
            "<rss version=\"2.0\"><channel></channel></rss>"
        ));
        assert!(!looks_like_rss_xml(
            "<html><body>Too Many Requests</body></html>"
        ));
        assert!(!looks_like_rss_xml(""));
    }
}

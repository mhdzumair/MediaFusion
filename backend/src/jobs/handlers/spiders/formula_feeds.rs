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
    scrapers::{
        ScrapedStream, SearchMeta, StreamFile, media_resolve, rss::parse_rss_xml, stream_convert,
    },
    util::rate_limit,
};

use super::formula_racing::resolve_racing_files;

const SOURCE: &str = "FormulaFeeds";
const CATEGORY: &str = "formula_racing";

const CHROME_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

/// Reddit profile RSS endpoints — try `www` first, then `old.reddit.com`.
const REDDIT_EGORTECH_URLS: &[&str] = &[
    "https://www.reddit.com/user/egortech/submitted/.rss",
    "https://old.reddit.com/user/egortech/submitted/.rss",
];

const REDDIT_MOTORSPORTS_URLS: &[&str] = &[
    "https://www.reddit.com/r/MotorsportsReplays/.rss",
    "https://old.reddit.com/r/MotorsportsReplays/.rss",
];

/// Motorsport categories accepted from the MotorsportsReplays subreddit feed.
const MOTORSPORTS_CATEGORIES: &[&str] = &["formula_racing", "motogp_racing"];

#[derive(Clone, Copy)]
enum FeedFilter {
    /// Formula 1/2/3 keyword match (egortech profile).
    FormulaOnly,
    /// Any recognised motorsport category (MotorsportsReplays subreddit).
    Motorsports,
}

#[derive(Clone, Copy)]
enum FeedKind {
    Bt4g,
    Reddit,
}

fn feed_request(client: &Client, kind: FeedKind, url: &str) -> RequestBuilder {
    let mut req = client.get(url).timeout(std::time::Duration::from_secs(30));

    match kind {
        FeedKind::Bt4g => {
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

async fn fetch_feed_xml(
    client: &Client,
    kind: FeedKind,
    urls: &[&str],
    label: &str,
) -> Option<String> {
    if matches!(kind, FeedKind::Reddit) {
        // Reddit rate-limits aggressive polling — match formulio's ~3s pacing.
        rate_limit::wait("formula_feeds_reddit", 3).await;
    }

    for (attempt, url) in urls.iter().enumerate() {
        let resp = match feed_request(client, kind, url).send().await {
            Ok(r) => r,
            Err(e) => {
                warn!("{SOURCE}/{label}: fetch failed for {url}: {e}");
                continue;
            }
        };

        let status = resp.status();
        if status.is_success() {
            match resp.text().await {
                Ok(body) if !body.trim().is_empty() => {
                    debug!("{SOURCE}/{label}: fetched {} bytes from {url}", body.len());
                    return Some(body);
                }
                Ok(_) => warn!("{SOURCE}/{label}: empty body from {url}"),
                Err(e) => warn!("{SOURCE}/{label}: body read failed for {url}: {e}"),
            }
            continue;
        }

        warn!("{SOURCE}/{label}: HTTP {status} for {url}");

        if attempt + 1 < urls.len() && (status.as_u16() == 403 || status.as_u16() == 429) {
            let retry_secs = resp
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(5);
            debug!("{SOURCE}/{label}: retrying alternate URL after {retry_secs}s (HTTP {status})");
            tokio::time::sleep(std::time::Duration::from_secs(retry_secs)).await;
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

fn feed_item_matches(title: &str, filter: FeedFilter) -> Option<&'static str> {
    match filter {
        FeedFilter::FormulaOnly if formula_feed_matches(title) => Some(CATEGORY),
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
    if let Some(link) = item.link.as_deref().filter(|l| l.starts_with("magnet:")) {
        return Some(link.replace("&amp;", "&"));
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
    None
}

async fn classify_formula_release(
    title: &str,
    info_hash: &str,
    pool: &sqlx::PgPool,
    proxy_url: Option<&str>,
) -> (
    String,
    Option<i32>,
    &'static str,
    Vec<StreamFile>,
    parser::ParsedTitle,
) {
    let parsed = parser::parse_sports_title(title);
    let wwe_info = parser::classify_wwe_title(title);
    let racing_info = parser::parse_racing_title(title);
    let drive_to_survive = parser::classify_drive_to_survive(title);

    if let Some((series_title, season, episode)) = drive_to_survive {
        let episode_title = parsed
            .episode_title
            .clone()
            .unwrap_or_else(|| parser::clean_sports_title(title));
        let files = vec![StreamFile {
            file_index: 0,
            filename: episode_title,
            season_number: season,
            episode_number: episode,
        }];
        return (series_title, None, "series", files, parsed);
    }

    if let Some(ref info) = wwe_info {
        let episode_title = parser::clean_sports_title(title);
        let files = vec![StreamFile {
            file_index: 0,
            filename: episode_title,
            season_number: info.season_number,
            episode_number: info.episode_number,
        }];
        return (info.series_title.to_string(), None, "series", files, parsed);
    }

    if let Some(ref racing) = racing_info {
        let display_title = racing
            .session
            .clone()
            .unwrap_or_else(|| parser::clean_sports_title(title));
        let files = resolve_racing_files(
            SOURCE,
            info_hash,
            &[],
            None,
            &display_title,
            pool,
            proxy_url,
        )
        .await;
        return (
            racing.series_title.clone(),
            racing.year,
            "series",
            files,
            parsed,
        );
    }

    let clean = parsed.title.clone().unwrap_or_else(|| title.to_string());
    (clean, parsed.year, "movie", vec![], parsed)
}

async fn persist_formula_stream(
    pool: &sqlx::PgPool,
    title: &str,
    info_hash: &str,
    clean_title: String,
    year: Option<i32>,
    effective_media_type: &str,
    parsed: parser::ParsedTitle,
    files: Vec<StreamFile>,
    source: String,
    seeders: Option<i32>,
    size: Option<i64>,
    uploader: Option<String>,
    catalog: &str,
) {
    let media_id = media_resolve::find_or_create_sports_stub(
        pool,
        &clean_title,
        year,
        None,
        &effective_media_type.to_uppercase(),
        catalog,
    )
    .await
    .unwrap_or(0);

    if media_id > 0 {
        media_resolve::link_to_catalogs(pool, media_id, &[catalog]).await;
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

    let extra_files_to_persist = (files.len() > 1).then(|| files.clone());
    let stream = ScrapedStream {
        info_hash: info_hash.to_string(),
        name: title.to_string(),
        source,
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
    stream_convert::write_back_torrents(pool, &[stream], &meta, effective_media_type, None, None)
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
        let _ = crate::db::streams::upsert_stream_files(pool, info_hash, &entries).await;
    }
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

    let feeds: [FeedSpec; 7] = [
        FeedSpec {
            label: "BT4G",
            kind: FeedKind::Bt4g,
            urls: &["https://bt4gprx.com/search?q=formula+1&page=rss"],
            filter: FeedFilter::FormulaOnly,
            uploader: None,
        },
        FeedSpec {
            label: "BT4G",
            kind: FeedKind::Bt4g,
            urls: &["https://bt4gprx.com/search?q=formula+2&page=rss"],
            filter: FeedFilter::FormulaOnly,
            uploader: None,
        },
        FeedSpec {
            label: "BT4G",
            kind: FeedKind::Bt4g,
            urls: &["https://bt4gprx.com/search?q=formula+3&page=rss"],
            filter: FeedFilter::FormulaOnly,
            uploader: None,
        },
        FeedSpec {
            label: "BT4G",
            kind: FeedKind::Bt4g,
            urls: &["https://bt4gprx.com/search?q=F1..Grand.Prix.SkyUHD.2160P&page=rss"],
            filter: FeedFilter::FormulaOnly,
            uploader: None,
        },
        FeedSpec {
            label: "BT4G",
            kind: FeedKind::Bt4g,
            urls: &["https://bt4gprx.com/search?q=F1..Grand.Prix.SkyF1HD.1080P&page=rss"],
            filter: FeedFilter::FormulaOnly,
            uploader: None,
        },
        FeedSpec {
            label: "Reddit/egortech",
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

    let mut total_written = 0usize;

    for feed in feeds {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        rate_limit::wait("formula_feeds", 2).await;

        let Some(xml) = fetch_feed_xml(client, feed.kind, feed.urls, feed.label).await else {
            continue;
        };

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
                classify_formula_release(&title, &info_hash, pool, proxy_url).await;

            let source = format!("{SOURCE}/{}", feed.label);
            info!(
                "{source}: ✓ title=\"{title}\" info_hash={info_hash} clean_title=\"{clean_title}\" \
                 media_type={effective_media_type} catalog={catalog}"
            );

            persist_formula_stream(
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

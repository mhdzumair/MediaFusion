/// Fighting sports RSS spider — WWE, UFC, MMA, AEW, Bellator via BT4G/Knaben.
///
/// Mirrors the formula_feeds pattern so combat-sports content keeps scraping when
/// ext.to is blocked. Streams are deduped by info_hash at store time.
use async_trait::async_trait;
use regex::Regex;
use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, USER_AGENT};
use reqwest::{Client, RequestBuilder};
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::rss::parse_rss_xml,
    util::rate_limit,
};

use super::sports_rss_common::{
    SportsRssPersistCtx, classify_sports_rss_release, persist_sports_rss_stream,
};

const SOURCE: &str = "FightingFeeds";
const CATEGORY: &str = "fighting";

const CHROME_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const BT4G_WWE: &str = "https://bt4gprx.com/search?q=WWE&page=rss";
const BT4G_UFC: &str = "https://bt4gprx.com/search?q=UFC&page=rss";
const BT4G_MMA: &str = "https://bt4gprx.com/search?q=MMA&page=rss";
const BT4G_AEW: &str = "https://bt4gprx.com/search?q=AEW&page=rss";
const BT4G_BELLATOR: &str = "https://bt4gprx.com/search?q=Bellator&page=rss";
const BT4G_BOXING: &str = "https://bt4gprx.com/search?q=Boxing&page=rss";

const KNABEN_WWE: &str = "https://rss.knaben.org/WWE//20";
const KNABEN_UFC: &str = "https://rss.knaben.org/UFC//20";
const KNABEN_MMA: &str = "https://rss.knaben.org/MMA//20";

#[derive(Clone, Copy)]
enum FeedKind {
    Bt4g,
    Knaben,
}

fn feed_request(client: &Client, kind: FeedKind, url: &str) -> RequestBuilder {
    let mut req = client.get(url).timeout(std::time::Duration::from_secs(30));
    if matches!(kind, FeedKind::Bt4g | FeedKind::Knaben) {
        req = req
            .header(USER_AGENT, CHROME_UA)
            .header(
                ACCEPT,
                "application/rss+xml, application/xml, text/xml, */*",
            )
            .header(ACCEPT_LANGUAGE, "en-US,en;q=0.9");
    }
    req
}

fn looks_like_rss_xml(body: &str) -> bool {
    let trimmed = body.trim_start();
    trimmed.starts_with("<?xml") || trimmed.starts_with("<feed") || trimmed.starts_with("<rss")
}

async fn fetch_feed_xml(client: &Client, kind: FeedKind, url: &str, label: &str) -> Option<String> {
    match feed_request(client, kind, url).send().await {
        Ok(resp) if resp.status().is_success() => {
            let body = resp.text().await.ok()?;
            if looks_like_rss_xml(&body) && !body.trim().is_empty() {
                debug!("{SOURCE}/{label}: fetched {} bytes from {url}", body.len());
                return Some(body);
            }
            warn!("{SOURCE}/{label}: non-RSS body from {url}");
        }
        Ok(resp) => warn!("{SOURCE}/{label}: HTTP {} for {url}", resp.status()),
        Err(e) => warn!("{SOURCE}/{label}: fetch failed for {url}: {e}"),
    }
    None
}

fn fighting_feed_matches(title: &str) -> bool {
    parser::detect_sports_category(title) == Some(CATEGORY)
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
    if let Some(desc) = &item.description
        && let Some(m) = extract_magnet_from_text(desc)
    {
        return Some(m);
    }
    if let Some(guid) = item.guid.as_deref().map(str::trim)
        && let Some(hash) = parser::extract_info_hash(guid)
    {
        return Some(format!("magnet:?xt=urn:btih:{hash}"));
    }
    None
}

async fn scrape_fighting_feeds(ctx: &JobCtx) -> Result<(), JobError> {
    let client = &ctx.state.http;
    let pool = &ctx.state.pool;
    let proxy_url = ctx.state.config.requests_proxy_url.as_deref();
    let current_year = chrono::Utc::now().format("%Y").to_string();
    let persist_ctx = SportsRssPersistCtx {
        http: client,
        tmdb_api_key: ctx.state.config.tmdb_api_key.as_deref(),
        cinemeta_fallback: ctx.state.config.imdb_cinemeta_fallback_enabled,
    };

    struct FeedSpec {
        label: &'static str,
        kind: FeedKind,
        url: &'static str,
    }

    let feeds = [
        FeedSpec {
            label: "BT4G/WWE",
            kind: FeedKind::Bt4g,
            url: BT4G_WWE,
        },
        FeedSpec {
            label: "BT4G/UFC",
            kind: FeedKind::Bt4g,
            url: BT4G_UFC,
        },
        FeedSpec {
            label: "BT4G/MMA",
            kind: FeedKind::Bt4g,
            url: BT4G_MMA,
        },
        FeedSpec {
            label: "BT4G/AEW",
            kind: FeedKind::Bt4g,
            url: BT4G_AEW,
        },
        FeedSpec {
            label: "BT4G/Bellator",
            kind: FeedKind::Bt4g,
            url: BT4G_BELLATOR,
        },
        FeedSpec {
            label: "BT4G/Boxing",
            kind: FeedKind::Bt4g,
            url: BT4G_BOXING,
        },
        FeedSpec {
            label: "Knaben/WWE",
            kind: FeedKind::Knaben,
            url: KNABEN_WWE,
        },
        FeedSpec {
            label: "Knaben/UFC",
            kind: FeedKind::Knaben,
            url: KNABEN_UFC,
        },
        FeedSpec {
            label: "Knaben/MMA",
            kind: FeedKind::Knaben,
            url: KNABEN_MMA,
        },
    ];

    let mut total_written = 0usize;

    for feed in feeds {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        rate_limit::wait("fighting_feeds", 2).await;

        let Some(xml) = fetch_feed_xml(client, feed.kind, feed.url, feed.label).await else {
            continue;
        };

        let items = parse_rss_xml(&xml);
        let mut feed_written = 0usize;

        for item in items {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let title = match item.title.as_deref().filter(|t| !t.trim().is_empty()) {
                Some(t) => t.trim().to_string(),
                None => continue,
            };

            if !fighting_feed_matches(&title) {
                continue;
            }

            if !title.contains(&current_year) {
                continue;
            }

            let Some(magnet) = magnet_from_rss_item(&item) else {
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
                 media_type={effective_media_type}"
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
                None,
                CATEGORY,
                Some(&persist_ctx),
            )
            .await;

            feed_written += 1;
            total_written += 1;
        }

        info!("{SOURCE}/{}: {feed_written} streams written", feed.label);
    }

    info!("{SOURCE}: {total_written} streams written from RSS feeds");
    Ok(())
}

pub struct FightingFeedsCrawl;

#[async_trait]
impl JobHandler for FightingFeedsCrawl {
    const QUEUE: &'static str = "spider_fighting_feeds";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        scrape_fighting_feeds(&ctx).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fighting_feed_filter_accepts_wwe_and_ufc() {
        assert!(fighting_feed_matches(
            "WWE.SmackDown.2026.07.10.720p.WEBRip"
        ));
        assert!(fighting_feed_matches("UFC.Fight.Night.240.1080p.WEB.h264"));
        assert!(!fighting_feed_matches("Formula 1 British Grand Prix 2026"));
    }
}

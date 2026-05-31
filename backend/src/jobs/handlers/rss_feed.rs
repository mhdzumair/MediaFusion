use async_trait::async_trait;
use tracing::{info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::rss::scrape_feed,
};

pub struct RssFeedScraper;

/// A row from the rss_feed table.
struct RssFeedRow {
    id: i32,
    url: String,
    name: String,
    source: Option<String>,
    parsing_patterns: Option<serde_json::Value>,
    filters: Option<serde_json::Value>,
    auto_detect_catalog: bool,
    torrent_type: String,
}

async fn fetch_active_feeds(pool: &sqlx::PgPool) -> Result<Vec<RssFeedRow>, JobError> {
    let rows = sqlx::query(
        r#"
        SELECT id, url, name, source, parsing_patterns, filters, auto_detect_catalog, torrent_type::text
        FROM rss_feed WHERE is_active = true
        "#,
    )
    .fetch_all(pool)
    .await?;

    let mut feeds = Vec::with_capacity(rows.len());
    for row in rows {
        use sqlx::Row;
        feeds.push(RssFeedRow {
            id: row.try_get("id")?,
            url: row.try_get("url")?,
            name: row.try_get("name")?,
            source: row.try_get("source")?,
            parsing_patterns: row.try_get("parsing_patterns")?,
            filters: row.try_get("filters")?,
            auto_detect_catalog: row.try_get("auto_detect_catalog")?,
            torrent_type: row.try_get("torrent_type")?,
        });
    }
    Ok(feeds)
}

#[async_trait]
impl JobHandler for RssFeedScraper {
    const QUEUE: &'static str = "rss_feed";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let feeds = fetch_active_feeds(&ctx.state.pool_ro).await?;

        if feeds.is_empty() {
            info!("rss_feed: no active feeds found");
            return Ok(());
        }

        info!("rss_feed: processing {} active feed(s)", feeds.len());

        for feed in &feeds {
            if ctx.is_cancelled() {
                warn!("rss_feed: cancellation requested, stopping early");
                return Err(JobError::Cancelled);
            }

            let feed_torrent_type =
                crate::scrapers::torrent_metadata::parse_torrent_type_str(&feed.torrent_type);
            let result = scrape_feed(
                &ctx.state.pool,
                &ctx.state.http,
                feed.id,
                &feed.url,
                &feed.name,
                feed.source.as_deref(),
                feed.parsing_patterns.as_ref(),
                feed.filters.as_ref(),
                feed.auto_detect_catalog,
                feed_torrent_type,
                ctx.state.config.tmdb_api_key.as_deref(),
                ctx.state.config.imdb_cinemeta_fallback_enabled,
            )
            .await;

            info!(
                "rss_feed: feed_id={} name={:?} — found={} processed={} skipped={} errors={}",
                feed.id,
                feed.name,
                result.items_found,
                result.items_processed,
                result.items_skipped,
                result.errors,
            );

            if result.errors > 0 {
                warn!(
                    "rss_feed: feed_id={} name={:?} had {} error(s)",
                    feed.id, feed.name, result.errors
                );
            }
        }

        info!("rss_feed: all feeds processed");
        Ok(())
    }
}

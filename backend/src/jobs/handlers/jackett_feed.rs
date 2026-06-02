use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use tracing::{debug, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{jackett, media_resolve, stream_convert},
};

pub struct JackettFeedScraper;

// ─── Jackett API response shapes ──────────────────────────────────────────────

#[derive(Debug, serde::Deserialize)]
struct JackettResponse {
    #[serde(rename = "Results", default)]
    results: Vec<jackett::JackettResult>,
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SEEN_KEY: &str = "jackett_feed:seen";
const SEEN_TTL: i64 = 259_200; // 3 days

// ─── Job handler ──────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for JackettFeedScraper {
    const QUEUE: &'static str = "jackett_feed";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config = &ctx.state.config;

        let (base_url, api_key) = match (&config.jackett_url, &config.jackett_api_key) {
            (Some(u), Some(k)) => (u.clone(), k.clone()),
            _ => {
                debug!("jackett_feed: jackett not configured, skipping");
                return Ok(());
            }
        };

        let client = &ctx.state.http;
        let redis = &ctx.state.redis;
        let pool = &ctx.state.pool;
        let query_timeout = std::time::Duration::from_secs(15);

        if ctx.cancel.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        let resp = match client
            .get(format!("{base_url}/api/v2.0/indexers/all/results"))
            .query(&[
                ("apikey", api_key.as_str()),
                ("t", "search"),
                ("cat", "2000,5000"),
                ("q", ""),
            ])
            .timeout(std::time::Duration::from_secs(60))
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!("jackett_feed: search request failed: {e}");
                return Ok(());
            }
        };

        let feed: JackettResponse = match resp.json().await {
            Ok(f) => f,
            Err(e) => {
                warn!("jackett_feed: failed to parse response: {e}");
                return Ok(());
            }
        };

        debug!("jackett_feed: received {} results", feed.results.len());

        let mut candidates: Vec<jackett::JackettResult> = Vec::new();
        for result in feed.results {
            if ctx.cancel.is_cancelled() {
                debug!("jackett_feed: cancelled during processing");
                return Err(JobError::Cancelled);
            }

            let info_hash = jackett::resolve_result_info_hash(&result);
            let Some(info_hash) = info_hash else {
                continue;
            };

            let seen: bool = redis
                .sismember::<bool, _, _>(SEEN_KEY, &info_hash)
                .await
                .unwrap_or(false);
            if seen {
                continue;
            }

            candidates.push(result);
        }

        let processed = jackett::process_feed_results(client, candidates, query_timeout).await;

        let cfg = &ctx.state.config;
        for (stream, media_type) in processed {
            let hash = stream.info_hash.clone();
            let _ = redis.sadd::<(), _, _>(SEEN_KEY, hash.clone()).await;
            let _ = redis.expire::<i64, _>(SEEN_KEY, SEEN_TTL, None).await;

            let is_series = media_type == "series";
            if let Some(meta) = media_resolve::search_meta_for_scraped(
                pool,
                &ctx.state.http,
                &stream,
                is_series,
                cfg.tmdb_api_key.as_deref(),
                cfg.imdb_cinemeta_fallback_enabled,
                &cfg.anime_metadata_source_order,
                &cfg.metadata_primary_source,
            )
            .await
            {
                stream_convert::write_back_torrents(
                    pool,
                    std::slice::from_ref(&stream),
                    &meta,
                    media_type,
                    None,
                    None,
                )
                .await;
            } else {
                debug!(
                    "jackett_feed: skipped {} ({}) — metadata unresolved",
                    hash, media_type
                );
            }
        }

        Ok(())
    }
}

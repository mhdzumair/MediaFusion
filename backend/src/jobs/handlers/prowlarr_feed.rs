use std::collections::HashSet;

use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use tracing::{debug, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{media_resolve, persist, prowlarr},
};

pub struct ProwlarrFeedScraper;

// ─── Prowlarr API response shapes ─────────────────────────────────────────────

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct IndexerInfo {
    id: i64,
    #[serde(default)]
    enable: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct IndexerStatus {
    indexer_id: i64,
    #[serde(default)]
    disabled_till: Option<String>,
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SEEN_KEY: &str = "prowlarr_feed:seen";
const SEEN_TTL: i64 = 259_200; // 3 days
const BATCH_SIZE: usize = 5;

// ─── Job handler ──────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for ProwlarrFeedScraper {
    const QUEUE: &'static str = "prowlarr_feed";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config = &ctx.state.config;

        let (base_url, api_key) = match (&config.prowlarr_url, &config.prowlarr_api_key) {
            (Some(u), Some(k)) => (u.clone(), k.clone()),
            _ => {
                debug!("prowlarr_feed: prowlarr not configured, skipping");
                return Ok(());
            }
        };

        let client = &ctx.state.http;
        let redis = &ctx.state.redis;
        let pool = &ctx.state.pool;
        let query_timeout = std::time::Duration::from_secs(15);

        let privacy_by_id = prowlarr::fetch_indexer_privacy_map(client, &base_url, &api_key).await;

        // Fetch enabled indexers
        let indexers: Vec<IndexerInfo> = match client
            .get(format!("{base_url}/api/v1/indexer"))
            .header("X-Api-Key", &api_key)
            .timeout(std::time::Duration::from_secs(15))
            .send()
            .await
        {
            Ok(r) => r.json().await.unwrap_or_default(),
            Err(e) => {
                warn!("prowlarr_feed: failed to fetch indexers: {e}");
                return Ok(());
            }
        };

        // Fetch indexer statuses to skip unhealthy ones
        let statuses: Vec<IndexerStatus> = match client
            .get(format!("{base_url}/api/v1/indexerstatus"))
            .header("X-Api-Key", &api_key)
            .timeout(std::time::Duration::from_secs(15))
            .send()
            .await
        {
            Ok(r) => r.json().await.unwrap_or_default(),
            Err(e) => {
                warn!("prowlarr_feed: failed to fetch indexer statuses: {e}");
                vec![]
            }
        };

        let now = chrono::Utc::now();
        let disabled_ids: HashSet<i64> = statuses
            .into_iter()
            .filter_map(|s| {
                let dt_str = s.disabled_till?;
                let dt_str = dt_str.replace('Z', "+00:00");
                let dt = chrono::DateTime::parse_from_rfc3339(&dt_str).ok()?;
                if dt > now {
                    Some(s.indexer_id)
                } else {
                    None
                }
            })
            .collect();

        let healthy_ids: Vec<i64> = indexers
            .into_iter()
            .filter(|i| i.enable && !disabled_ids.contains(&i.id))
            .map(|i| i.id)
            .collect();

        debug!(
            "prowlarr_feed: {} healthy indexers to poll",
            healthy_ids.len()
        );

        // Process in batches of BATCH_SIZE
        for batch in healthy_ids.chunks(BATCH_SIZE) {
            if ctx.cancel.is_cancelled() {
                debug!("prowlarr_feed: cancelled between batches");
                return Err(JobError::Cancelled);
            }

            let mut params: Vec<(&str, String)> =
                vec![("query", String::new()), ("type", "search".to_string())];
            for &id in batch {
                params.push(("indexerIds[]", id.to_string()));
            }
            params.push(("categories[]", "2000".to_string()));
            params.push(("categories[]", "5000".to_string()));

            let results: Vec<prowlarr::SearchResult> = match client
                .get(format!("{base_url}/api/v1/search"))
                .header("X-Api-Key", &api_key)
                .query(&params)
                .timeout(std::time::Duration::from_secs(30))
                .send()
                .await
            {
                Ok(r) => r.json().await.unwrap_or_default(),
                Err(e) => {
                    warn!("prowlarr_feed: batch search failed: {e}");
                    continue;
                }
            };

            let mut candidates: Vec<prowlarr::SearchResult> = Vec::new();
            for result in results {
                let Some(info_hash) = prowlarr::resolve_result_info_hash(&result) else {
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

            let processed =
                prowlarr::process_feed_results(client, candidates, &privacy_by_id, query_timeout)
                    .await;

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
                    persist::write_back(
                        std::slice::from_ref(&stream),
                        pool,
                        &meta,
                        media_type,
                        None,
                        None,
                    )
                    .await;
                } else {
                    debug!(
                        "prowlarr_feed: skipped {} ({}) — metadata unresolved",
                        hash, media_type
                    );
                }
            }
        }

        Ok(())
    }
}

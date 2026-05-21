use std::collections::HashSet;

use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use tracing::{debug, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{media_resolve, persist, ScrapedStream, StreamFile},
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

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct FeedResult {
    #[serde(default)]
    info_hash: Option<String>,
    #[serde(default)]
    magnet_url: Option<String>,
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    indexer: Option<String>,
    #[serde(default)]
    seeders: Option<i32>,
    #[serde(default)]
    size: Option<i64>,
    #[serde(default)]
    categories: Vec<serde_json::Value>,
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SEEN_KEY: &str = "prowlarr_feed:seen";
const SEEN_TTL: i64 = 259_200; // 3 days
const BATCH_SIZE: usize = 5;

// ─── Helpers ─────────────────────────────────────────────────────────────────

fn media_type_from_categories(categories: &[serde_json::Value]) -> &'static str {
    for cat in categories {
        let id = cat
            .get("id")
            .and_then(|v| v.as_i64())
            .unwrap_or_else(|| cat.as_i64().unwrap_or(-1));
        if (2000..3000).contains(&id) {
            return "movie";
        }
        if (5000..6000).contains(&id) {
            return "series";
        }
    }
    "movie"
}

fn resolve_info_hash(result: &FeedResult) -> Option<String> {
    result
        .info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40)
        .or_else(|| {
            result
                .magnet_url
                .as_deref()
                .and_then(parser::extract_info_hash)
        })
}

fn build_scraped_stream(result: &FeedResult) -> Option<ScrapedStream> {
    let title = result.title.as_deref().unwrap_or("").trim().to_string();
    if title.is_empty() {
        return None;
    }

    let info_hash = resolve_info_hash(result)?;

    let media_type = media_type_from_categories(&result.categories);
    let parsed = parser::parse_title(&title);

    let files: Vec<StreamFile> = if media_type == "series" {
        crate::scrapers::prowlarr::build_series_files(&parsed, None, None)
    } else {
        vec![]
    };

    let source = result
        .indexer
        .clone()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "Prowlarr".to_string());

    Some(ScrapedStream {
        info_hash,
        name: title,
        source,
        seeders: result.seeders,
        size: result.size,
        parsed,
        files,
        is_cached: false,
    })
}

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

            // Build query params: empty query, video categories, batch of indexer IDs
            let mut params: Vec<(&str, String)> =
                vec![("query", String::new()), ("type", "search".to_string())];
            for &id in batch {
                params.push(("indexerIds[]", id.to_string()));
            }
            params.push(("categories[]", "2000".to_string()));
            params.push(("categories[]", "5000".to_string()));

            let results: Vec<FeedResult> = match client
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

            let mut new_streams: Vec<(ScrapedStream, &'static str)> = Vec::new();

            for result in &results {
                let info_hash = match resolve_info_hash(result) {
                    Some(h) => h,
                    None => continue,
                };

                // Dedup via Redis
                let seen: bool = redis
                    .sismember::<bool, _, _>(SEEN_KEY, &info_hash)
                    .await
                    .unwrap_or(false);
                if seen {
                    continue;
                }

                if let Some(stream) = build_scraped_stream(result) {
                    let media_type = media_type_from_categories(&result.categories);
                    new_streams.push((stream, media_type));
                }
            }

            let cfg = &ctx.state.config;
            for (stream, media_type) in &new_streams {
                let hash = stream.info_hash.clone();
                let _ = redis.sadd::<(), _, _>(SEEN_KEY, hash.clone()).await;
                let _ = redis.expire::<i64, _>(SEEN_KEY, SEEN_TTL, None).await;

                let is_series = *media_type == "series";
                if let Some(meta) = media_resolve::search_meta_for_scraped(
                    pool,
                    &ctx.state.http,
                    stream,
                    is_series,
                    cfg.tmdb_api_key.as_deref(),
                    cfg.imdb_cinemeta_fallback_enabled,
                    &cfg.anime_metadata_source_order,
                    &cfg.metadata_primary_source,
                )
                .await
                {
                    persist::write_back(
                        std::slice::from_ref(stream),
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

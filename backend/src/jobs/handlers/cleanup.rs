use async_trait::async_trait;
use fred::prelude::*;
use tracing::{info, warn};

use crate::jobs::{
    error::JobError,
    handler::{JobCtx, JobHandler},
};

pub struct Cleanup;

/// Redis key patterns for scraper-related caches.
const SCRAPER_PATTERNS: &[&str] = &[
    "prowlarr:*",
    "jackett:*",
    "torznab:*",
    "newznab:*",
    "torrentio:*",
    "zilean:*",
    "mediafusion:*",
    "easynews:*",
    "torbox:*",
    "telegram_scraper:*",
    "public_indexer:*",
    "public_usenet:*",
];

/// Redis key patterns for provider result caches.
const PROVIDER_CACHE_PATTERNS: &[&str] = &["mf_cache:*", "debrid_cache:*"];

/// Scan Redis for all keys matching a glob pattern (full scan, all pages).
async fn scan_all_keys(redis: &fred::clients::Client, pattern: &str) -> Vec<String> {
    let mut all_keys: Vec<String> = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = redis
            .scan_page(cursor.clone(), pattern.to_string(), Some(500), None)
            .await;

        let (next_cursor, keys) = match result {
            Ok(value) => parse_scan_value(value),
            Err(e) => {
                warn!("cleanup: Redis SCAN error for pattern {pattern}: {e}");
                break;
            }
        };

        all_keys.extend(keys);

        if next_cursor == "0" {
            break;
        }
        cursor = next_cursor;
    }
    all_keys
}

fn parse_scan_value(value: fred::types::Value) -> (String, Vec<String>) {
    if let fred::types::Value::Array(arr) = value {
        if arr.len() == 2 {
            let cursor = match &arr[0] {
                fred::types::Value::String(s) => s.to_string(),
                fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
                fred::types::Value::Integer(n) => n.to_string(),
                other => format!("{other:?}"),
            };
            let keys = if let fred::types::Value::Array(key_arr) = &arr[1] {
                key_arr
                    .iter()
                    .filter_map(|v| match v {
                        fred::types::Value::String(s) => Some(s.to_string()),
                        fred::types::Value::Bytes(b) => {
                            Some(String::from_utf8_lossy(b).to_string())
                        }
                        _ => None,
                    })
                    .collect()
            } else {
                Vec::new()
            };
            return (cursor, keys);
        }
    }
    ("0".to_string(), Vec::new())
}

/// For a slice of keys, delete those whose TTL == -1 (persistent / no expiry).
async fn delete_stale_keys(redis: &fred::clients::Client, keys: &[String]) -> (u64, u64) {
    let mut checked: u64 = 0;
    let mut deleted: u64 = 0;

    for key in keys {
        let ttl: i64 = redis.ttl(key).await.unwrap_or(0);
        checked += 1;
        if ttl == -1 {
            match redis.del::<(), _>(key).await {
                Ok(()) => deleted += 1,
                Err(e) => warn!("cleanup: failed to delete stale key {key}: {e}"),
            }
        }
    }

    (checked, deleted)
}

async fn run_scraper_task(ctx: &JobCtx) -> Result<(), JobError> {
    let redis = &ctx.state.redis;
    let mut total_checked: u64 = 0;
    let mut total_deleted: u64 = 0;

    for pattern in SCRAPER_PATTERNS {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        let keys = scan_all_keys(redis, pattern).await;
        let (checked, deleted) = delete_stale_keys(redis, &keys).await;
        total_checked += checked;
        total_deleted += deleted;

        if checked > 0 {
            info!("cleanup[scraper]: pattern={pattern} checked={checked} deleted={deleted}");
        }
    }

    info!("cleanup[scraper]: done — total_checked={total_checked} total_deleted={total_deleted}");
    Ok(())
}

async fn run_cache_task(ctx: &JobCtx) -> Result<(), JobError> {
    let redis = &ctx.state.redis;
    let mut total_checked: u64 = 0;
    let mut total_deleted: u64 = 0;

    for pattern in PROVIDER_CACHE_PATTERNS {
        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        let keys = scan_all_keys(redis, pattern).await;
        let (checked, deleted) = delete_stale_keys(redis, &keys).await;
        total_checked += checked;
        total_deleted += deleted;

        if checked > 0 {
            info!("cleanup[cache]: pattern={pattern} checked={checked} deleted={deleted}");
        }
    }

    let debrid_removed =
        crate::providers::torrents::cache_federation::cleanup_all_services(redis).await;
    if debrid_removed > 0 {
        info!("cleanup[cache]: debrid_cache expired entries removed={debrid_removed}");
    }

    info!("cleanup[cache]: done — total_checked={total_checked} total_deleted={total_deleted}");
    Ok(())
}

#[async_trait]
impl JobHandler for Cleanup {
    const QUEUE: &'static str = "cleanup";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let task = args
            .get("task")
            .and_then(|v| v.as_str())
            .unwrap_or("scraper_task");

        match task {
            "scraper_task" => run_scraper_task(&ctx).await,
            "cache" => run_cache_task(&ctx).await,
            other => {
                warn!("cleanup: unknown task type '{other}', defaulting to scraper_task");
                run_scraper_task(&ctx).await
            }
        }
    }
}

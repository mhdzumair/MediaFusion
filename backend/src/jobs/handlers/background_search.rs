use std::collections::HashMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use fred::prelude::{HashesInterface, SetsInterface};
use serde::Deserialize;
use tracing::{debug, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    scrapers::{persist, SearchMeta},
};

pub struct BackgroundSearch;

// ─── Constants ────────────────────────────────────────────────────────────────

const MOVIES_KEY: &str = "background_search:movies";
const SERIES_KEY: &str = "background_search:series";
const PROCESSING_KEY: &str = "background_search:processing";
const BATCH_SIZE: usize = 10;
/// Default re-scrape interval: 24 hours in seconds.
const DEFAULT_INTERVAL_SECS: f64 = 86_400.0;

// ─── Queue item value shape ───────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct QueueEntry {
    #[serde(default)]
    last_scrape: f64,
}

// ─── DB row helpers ───────────────────────────────────────────────────────────

struct MediaRow {
    media_id: i32,
    title: String,
    year: Option<i32>,
}

async fn lookup_movie(pool: &sqlx::PgPool, id: &str) -> Option<MediaRow> {
    let parsed_id: i32 = id.parse().ok()?;
    sqlx::query_as::<_, (i32, String, Option<i32>)>(
        "SELECT id, title, year FROM media WHERE id = $1 AND type = 'movie'",
    )
    .bind(parsed_id)
    .fetch_optional(pool)
    .await
    .ok()?
    .map(|(media_id, title, year)| MediaRow {
        media_id,
        title,
        year,
    })
}

async fn lookup_series_media(pool: &sqlx::PgPool, media_id: i32) -> Option<MediaRow> {
    sqlx::query_as::<_, (i32, String, Option<i32>)>(
        "SELECT id, title, year FROM media WHERE id = $1 AND type = 'series'",
    )
    .bind(media_id)
    .fetch_optional(pool)
    .await
    .ok()?
    .map(|(id, title, year)| MediaRow {
        media_id: id,
        title,
        year,
    })
}

async fn lookup_imdb_id(pool: &sqlx::PgPool, media_id: i32) -> Option<String> {
    let row: Option<(String,)> = sqlx::query_as(
        "SELECT external_id FROM media_external_id WHERE media_id = $1 AND provider = 'imdb' LIMIT 1",
    )
    .bind(media_id)
    .fetch_optional(pool)
    .await
    .ok()?;
    row.map(|(id,)| id)
}

// ─── Timing ──────────────────────────────────────────────────────────────────

fn now_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

// ─── Job handler ──────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for BackgroundSearch {
    const QUEUE: &'static str = "background_search";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let redis = &ctx.state.redis;
        let pool = &ctx.state.pool;
        let config = &ctx.state.config;
        let client = &ctx.state.http;

        let now = now_f64();

        // Re-scrape interval: use the configured prowlarr/jackett TTL as proxy,
        // falling back to 24 h.
        let interval_secs = if config.prowlarr_url.is_some() {
            config.prowlarr_search_ttl as f64
        } else if config.jackett_url.is_some() {
            config.jackett_search_ttl as f64
        } else {
            DEFAULT_INTERVAL_SECS
        };

        // Collect due items from both queues
        let movies_raw: HashMap<String, String> = redis
            .hgetall::<HashMap<String, String>, _>(MOVIES_KEY)
            .await
            .unwrap_or_default();

        let series_raw: HashMap<String, String> = redis
            .hgetall::<HashMap<String, String>, _>(SERIES_KEY)
            .await
            .unwrap_or_default();

        // Each item in the work queue: (key, media_type_queue_key, item_key)
        // item_key for movies = media_id string
        // item_key for series = "{media_id}:{season}:{episode}"
        let mut due: Vec<(String, &'static str)> = Vec::new();

        for (item_key, json_val) in &movies_raw {
            if due.len() >= BATCH_SIZE {
                break;
            }
            let entry: QueueEntry =
                serde_json::from_str(json_val).unwrap_or(QueueEntry { last_scrape: 0.0 });
            let is_due = entry.last_scrape == 0.0 || (now - entry.last_scrape) >= interval_secs;
            if !is_due {
                continue;
            }
            let in_processing: bool = redis
                .sismember::<bool, _, _>(PROCESSING_KEY, item_key.as_str())
                .await
                .unwrap_or(false);
            if in_processing {
                continue;
            }
            due.push((item_key.clone(), MOVIES_KEY));
        }

        for (item_key, json_val) in &series_raw {
            if due.len() >= BATCH_SIZE {
                break;
            }
            let entry: QueueEntry =
                serde_json::from_str(json_val).unwrap_or(QueueEntry { last_scrape: 0.0 });
            let is_due = entry.last_scrape == 0.0 || (now - entry.last_scrape) >= interval_secs;
            if !is_due {
                continue;
            }
            let in_processing: bool = redis
                .sismember::<bool, _, _>(PROCESSING_KEY, item_key.as_str())
                .await
                .unwrap_or(false);
            if in_processing {
                continue;
            }
            due.push((item_key.clone(), SERIES_KEY));
        }

        debug!("background_search: {} items due for re-scrape", due.len());

        for (item_key, queue_key) in due {
            if ctx.cancel.is_cancelled() {
                debug!("background_search: cancelled between items");
                return Err(JobError::Cancelled);
            }

            // Mark as processing
            let _ = redis
                .sadd::<(), _, _>(PROCESSING_KEY, item_key.as_str())
                .await;

            let result = process_item(&item_key, queue_key, pool, client, redis, config, now).await;

            if let Err(e) = result {
                warn!("background_search: item {item_key} failed: {e}");
            }

            // Always unmark processing
            let _ = redis
                .srem::<(), _, _>(PROCESSING_KEY, item_key.as_str())
                .await;
        }

        Ok(())
    }
}

async fn process_item(
    item_key: &str,
    queue_key: &'static str,
    pool: &sqlx::PgPool,
    client: &reqwest::Client,
    redis: &fred::clients::Client,
    config: &crate::config::AppConfig,
    now: f64,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let is_movie = queue_key == MOVIES_KEY;

    // Parse the item_key
    let (media_id_str, season, episode): (&str, Option<i32>, Option<i32>) = if is_movie {
        (item_key, None, None)
    } else {
        // Format: "{media_id}:{season}:{episode}"
        let parts: Vec<&str> = item_key.splitn(3, ':').collect();
        if parts.len() == 3 {
            let s: Option<i32> = parts[1].parse().ok();
            let e: Option<i32> = parts[2].parse().ok();
            (parts[0], s, e)
        } else {
            // Malformed key — remove from hash
            let _ = redis.hdel::<(), _, _>(queue_key, item_key).await;
            return Ok(());
        }
    };

    // Look up media in DB
    let media = if is_movie {
        match lookup_movie(pool, media_id_str).await {
            Some(m) => m,
            None => {
                debug!("background_search: media {media_id_str} not found, removing from queue");
                let _ = redis.hdel::<(), _, _>(queue_key, item_key).await;
                return Ok(());
            }
        }
    } else {
        let media_id: i32 = match media_id_str.parse() {
            Ok(id) => id,
            Err(_) => {
                let _ = redis.hdel::<(), _, _>(queue_key, item_key).await;
                return Ok(());
            }
        };
        match lookup_series_media(pool, media_id).await {
            Some(m) => m,
            None => {
                debug!("background_search: series {media_id_str} not found, removing from queue");
                let _ = redis.hdel::<(), _, _>(queue_key, item_key).await;
                return Ok(());
            }
        }
    };

    let imdb_id = lookup_imdb_id(pool, media.media_id).await;

    let meta = SearchMeta {
        media_id: crate::db::MediaId(media.media_id),
        imdb_id,
        title: media.title,
        year: media.year,
    };

    let media_type = if is_movie { "movie" } else { "series" };

    // Scrape from configured providers
    let max_process = 50usize;
    let max_time = Duration::from_secs(120);
    let query_timeout = Duration::from_secs(30);

    let mut all_streams = Vec::new();

    if let (Some(url), Some(key)) = (&config.prowlarr_url, &config.prowlarr_api_key) {
        let streams = crate::scrapers::prowlarr::scrape(
            client,
            url,
            key,
            &meta,
            media_type,
            season,
            episode,
            max_process,
            max_time,
            query_timeout,
        )
        .await;
        debug!(
            "background_search: prowlarr returned {} streams for {}",
            streams.len(),
            item_key
        );
        all_streams.extend(streams);
    }

    if let (Some(url), Some(key)) = (&config.jackett_url, &config.jackett_api_key) {
        let streams = crate::scrapers::jackett::scrape(
            client,
            url,
            key,
            &meta,
            media_type,
            season,
            episode,
            max_process,
            max_time,
            query_timeout,
        )
        .await;
        debug!(
            "background_search: jackett returned {} streams for {}",
            streams.len(),
            item_key
        );
        all_streams.extend(streams);
    }

    if !all_streams.is_empty() {
        persist::write_back(&all_streams, pool, &meta, media_type, season, episode).await;
    }

    // Update last_scrape timestamp in Redis
    let updated = serde_json::json!({
        "last_scrape": now,
        "added_at": now,
    });
    let _ = redis
        .hset::<(), _, _>(queue_key, (item_key, updated.to_string()))
        .await;

    Ok(())
}

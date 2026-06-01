use std::collections::HashMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use fred::prelude::{HashesInterface, SetsInterface};
use serde::Deserialize;
use tracing::{debug, warn};

use crate::{
    db::MediaType,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    models::user_data::UserData,
    scrapers::{background_queue, orchestrator, SearchMeta},
    state::AppState,
};

pub struct BackgroundSearch;

const BATCH_SIZE: usize = 10;

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
        "SELECT id, title, year FROM media WHERE id = $1 AND type = $2",
    )
    .bind(parsed_id)
    .bind(MediaType::Movie)
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
        "SELECT id, title, year FROM media WHERE id = $1 AND type = $2",
    )
    .bind(media_id)
    .bind(MediaType::Series)
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
        let config = &ctx.state.config;
        let now = now_f64();
        let interval_secs = (config.background_search_interval_hours * 3600) as f64;

        let movies_raw: HashMap<String, String> = redis
            .hgetall::<HashMap<String, String>, _>(background_queue::MOVIES_KEY)
            .await
            .unwrap_or_default();

        let series_raw: HashMap<String, String> = redis
            .hgetall::<HashMap<String, String>, _>(background_queue::SERIES_KEY)
            .await
            .unwrap_or_default();

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
                .sismember::<bool, _, _>(background_queue::PROCESSING_KEY, item_key.as_str())
                .await
                .unwrap_or(false);
            if in_processing {
                continue;
            }
            due.push((item_key.clone(), background_queue::MOVIES_KEY));
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
                .sismember::<bool, _, _>(background_queue::PROCESSING_KEY, item_key.as_str())
                .await
                .unwrap_or(false);
            if in_processing {
                continue;
            }
            due.push((item_key.clone(), background_queue::SERIES_KEY));
        }

        debug!("background_search: {} items due for re-scrape", due.len());

        for (item_key, queue_key) in due {
            if ctx.cancel.is_cancelled() {
                debug!("background_search: cancelled between items");
                return Err(JobError::Cancelled);
            }

            let _ = redis
                .sadd::<(), _, _>(background_queue::PROCESSING_KEY, item_key.as_str())
                .await;

            let result = process_item(&item_key, queue_key, &ctx.state, now).await;

            if let Err(e) = result {
                warn!("background_search: item {item_key} failed: {e}");
            }

            let _ = redis
                .srem::<(), _, _>(background_queue::PROCESSING_KEY, item_key.as_str())
                .await;
        }

        Ok(())
    }
}

async fn process_item(
    item_key: &str,
    queue_key: &'static str,
    state: &Arc<AppState>,
    now: f64,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let redis = &state.redis;
    let pool = &state.pool;
    let is_movie = queue_key == background_queue::MOVIES_KEY;

    let (media_id_str, season, episode): (&str, Option<i32>, Option<i32>) = if is_movie {
        (item_key, None, None)
    } else {
        let parts: Vec<&str> = item_key.splitn(3, ':').collect();
        if parts.len() == 3 {
            let s: Option<i32> = parts[1].parse().ok();
            let e: Option<i32> = parts[2].parse().ok();
            (parts[0], s, e)
        } else {
            let _ = redis.hdel::<(), _, _>(queue_key, item_key).await;
            return Ok(());
        }
    };

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

    orchestrator::run_background(
        state,
        &UserData::default(),
        &meta,
        media_type,
        season,
        episode,
        "background",
    )
    .await;

    let updated = serde_json::json!({
        "last_scrape": now,
        "added_at": now,
    });
    let _ = redis
        .hset::<(), _, _>(queue_key, (item_key, updated.to_string()))
        .await;

    Ok(())
}

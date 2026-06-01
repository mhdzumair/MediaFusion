use std::time::{SystemTime, UNIX_EPOCH};

use fred::prelude::HashesInterface;

pub const MOVIES_KEY: &str = "background_search:movies";
pub const SERIES_KEY: &str = "background_search:series";
pub const PROCESSING_KEY: &str = "background_search:processing";

pub fn movie_item_key(media_id: i32) -> String {
    media_id.to_string()
}

pub fn series_item_key(media_id: i32, season: i32, episode: i32) -> String {
    format!("{media_id}:{season}:{episode}")
}

/// Idempotently enqueue a media item for background re-scraping.
pub async fn enqueue(redis: &fred::clients::Client, key: &str, item_key: &str) {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    let entry = serde_json::json!({
        "last_scrape": 0.0,
        "added_at": now,
    });
    let _ = redis
        .hset::<(), _, _>(key, (item_key, entry.to_string()))
        .await;
}

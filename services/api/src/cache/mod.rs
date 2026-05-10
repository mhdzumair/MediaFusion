pub mod client;
pub mod codec;
pub mod stream_cache;

use std::collections::HashMap;

use chrono::Utc;
use fred::{clients::Client as RedisClient, prelude::*};
use serde_json::Value;
use tracing::warn;

/// Fetch a JSON value from Redis. Returns None on miss or error.
pub async fn get_json(client: &RedisClient, key: &str) -> Option<Value> {
    let bytes: Option<Vec<u8>> = client.get(key).await.ok()?;
    let bytes = bytes?;
    serde_json::from_slice(&bytes)
        .map_err(|e| warn!("cache JSON decode [{key}]: {e}"))
        .ok()
}

/// Store a JSON value in Redis with a TTL (seconds).
pub async fn set_json(client: &RedisClient, key: &str, value: &Value, ttl_secs: u64) {
    let bytes = match serde_json::to_vec(value) {
        Ok(b) => b,
        Err(e) => {
            warn!("cache JSON encode [{key}]: {e}");
            return;
        }
    };
    if let Err(e) = client
        .set::<(), _, _>(
            key,
            bytes.as_slice(),
            Some(Expiration::EX(ttl_secs as i64)),
            None,
            false,
        )
        .await
    {
        warn!("cache set [{key}]: {e}");
    }
}

/// Fetch raw bytes from Redis (for poster images).
pub async fn get_bytes(client: &RedisClient, key: &str) -> Option<Vec<u8>> {
    client.get(key).await.ok().flatten()
}

/// Check debrid cache status for a list of info hashes.
///
/// Reads from the `debrid_cache:{service}` Redis hash where each field is an
/// info_hash and the value is a Unix-second expiry timestamp. Mirrors Python's
/// `get_cached_status` (Redis-only path, no MediaFusion federation).
pub async fn get_debrid_cache_status(
    client: &RedisClient,
    service: &str,
    info_hashes: &[String],
) -> HashMap<String, bool> {
    if info_hashes.is_empty() {
        return HashMap::new();
    }

    let cache_key = format!("debrid_cache:{service}");
    let fields: Vec<String> = info_hashes.to_vec();

    let timestamps: Vec<Option<String>> =
        client.hmget(&cache_key, fields).await.unwrap_or_else(|e| {
            warn!("debrid_cache HMGET [{cache_key}]: {e}");
            vec![None; info_hashes.len()]
        });

    let now = Utc::now().timestamp();

    info_hashes
        .iter()
        .zip(timestamps)
        .map(|(hash, ts_opt)| {
            let cached = ts_opt
                .and_then(|s| s.parse::<i64>().ok())
                .map(|expiry| expiry > now)
                .unwrap_or(false);
            (hash.clone(), cached)
        })
        .collect()
}

/// Store raw bytes in Redis with a TTL (seconds).
pub async fn set_bytes(client: &RedisClient, key: &str, data: &[u8], ttl_secs: u64) {
    if let Err(e) = client
        .set::<(), _, _>(
            key,
            data,
            Some(Expiration::EX(ttl_secs as i64)),
            None,
            false,
        )
        .await
    {
        warn!("cache set_bytes [{key}]: {e}");
    }
}

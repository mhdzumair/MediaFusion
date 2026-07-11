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
/// info_hash and the value is a Unix-second expiry timestamp. When federation
/// is enabled, misses are checked against `mediafusion_url`.
pub async fn get_debrid_cache_status(
    client: &RedisClient,
    service: &str,
    info_hashes: &[String],
) -> HashMap<String, bool> {
    get_debrid_cache_status_federated(client, None, service, service, info_hashes, false, "").await
}

pub async fn get_debrid_cache_status_federated(
    client: &RedisClient,
    http: Option<&reqwest::Client>,
    cache_service: &str,
    federation_service: &str,
    info_hashes: &[String],
    sync_federation: bool,
    mediafusion_url: &str,
) -> HashMap<String, bool> {
    if info_hashes.is_empty() {
        return HashMap::new();
    }

    let cache_key = format!("debrid_cache:{cache_service}");
    let fields: Vec<String> = info_hashes.to_vec();

    let timestamps: Vec<Option<String>> =
        client.hmget(&cache_key, fields).await.unwrap_or_else(|e| {
            warn!("debrid_cache HMGET [{cache_key}]: {e}");
            vec![None; info_hashes.len()]
        });

    let now = Utc::now().timestamp();
    let mut result = HashMap::new();
    let mut expired: Vec<String> = Vec::new();
    let mut federation_needed: Vec<String> = Vec::new();

    for (hash, ts_opt) in info_hashes.iter().zip(timestamps) {
        match ts_opt {
            Some(ts_str) => {
                let expiry: i64 = ts_str.parse().unwrap_or(0);
                if expiry > now {
                    result.insert(hash.clone(), true);
                } else {
                    expired.push(hash.clone());
                    result.insert(hash.clone(), false);
                    federation_needed.push(hash.clone());
                }
            }
            None => {
                result.insert(hash.clone(), false);
                federation_needed.push(hash.clone());
            }
        }
    }

    if !expired.is_empty() {
        let _ = client.hdel::<(), _, _>(&cache_key, expired).await;
    }

    if sync_federation
        && !federation_needed.is_empty()
        && let Some(http) = http
    {
        let remote = crate::providers::torrents::cache_federation::fetch_federated_status(
            http,
            mediafusion_url,
            federation_service,
            &federation_needed,
        )
        .await;
        let mut to_store = Vec::new();
        for (hash, is_cached) in remote {
            if is_cached {
                result.insert(hash.clone(), true);
                to_store.push(hash);
            }
        }
        if !to_store.is_empty() {
            crate::providers::torrents::cache::store_cached_hashes(
                client,
                cache_service,
                &to_store,
            )
            .await;
        }
    }

    result
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

const MEDIA_DELETE_CACHE_PATTERNS: &[&str] = &[
    "catalog:browse:*",
    "catalog:count:*",
    "catalog:*",
    "movie_exists:*",
    "series_exists:*",
    "tv_exists:*",
    "meta_cache:*",
    "mf:*",
];

/// Clear catalog browse pages and metadata caches after media deletion.
pub async fn invalidate_catalog_and_metadata_caches(client: &RedisClient) {
    for pattern in MEDIA_DELETE_CACHE_PATTERNS {
        delete_by_pattern(client, pattern).await;
    }
}

async fn delete_by_pattern(client: &RedisClient, pattern: &str) {
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = client
            .scan_page(cursor.clone(), pattern.to_string(), Some(500), None)
            .await;

        let (next_cursor, keys) = match result {
            Ok(value) => parse_scan_value(value),
            Err(e) => {
                warn!("cache delete_by_pattern [{pattern}]: {e}");
                break;
            }
        };

        if !keys.is_empty() {
            let _ = client.del::<(), _>(keys).await;
        }

        if next_cursor == "0" {
            break;
        }
        cursor = next_cursor;
    }
}

fn parse_scan_value(value: fred::types::Value) -> (String, Vec<String>) {
    if let fred::types::Value::Array(arr) = value
        && arr.len() == 2
    {
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
                    fred::types::Value::Bytes(b) => Some(String::from_utf8_lossy(b).to_string()),
                    _ => None,
                })
                .collect()
        } else {
            Vec::new()
        };
        return (cursor, keys);
    }
    ("0".to_string(), Vec::new())
}

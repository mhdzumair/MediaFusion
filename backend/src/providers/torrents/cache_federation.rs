/// Cross-instance debrid cache federation and expiry cleanup.
use std::collections::HashMap;

use chrono::Utc;
use fred::{clients::Client as RedisClient, prelude::*};
use tracing::{info, warn};

use crate::models::user_data::StreamingProvider;

const CACHE_KEY_PREFIX: &str = "debrid_cache:";
const EXPIRY_DAYS_SECS: i64 = 7 * 86400;

/// Redis cache namespace for a provider (mirrors Python `get_cache_service_name`).
pub fn get_cache_service_name(provider: &StreamingProvider) -> String {
    provider.cache_service_name()
}

/// Same as [`get_cache_service_name`] when only raw fields are available.
pub fn cache_service_name(service: &str, stremthru_store_name: Option<&str>) -> String {
    if service == "stremthru" {
        if let Some(name) = stremthru_store_name.filter(|s| !s.is_empty()) {
            return name.to_string();
        }
    }
    service.to_string()
}

/// Whether magnet-cache entries should be stored (mirrors Python `store_cached_info_hashes` guard).
pub fn should_store_magnet_cache(
    provider_service: &str,
    store_stremthru_magnet_cache: bool,
) -> bool {
    provider_service != "stremthru" || store_stremthru_magnet_cache
}

pub async fn fetch_federated_status(
    http: &reqwest::Client,
    mediafusion_url: &str,
    service: &str,
    info_hashes: &[String],
) -> HashMap<String, bool> {
    if info_hashes.is_empty() || mediafusion_url.is_empty() {
        return HashMap::new();
    }

    let url = format!(
        "{}/streaming_provider/cache/status",
        mediafusion_url.trim_end_matches('/')
    );
    let resp = match http
        .post(&url)
        .json(&serde_json::json!({
            "service": service,
            "info_hashes": info_hashes,
        }))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!("debrid_cache federation fetch [{service}]: {e}");
            return HashMap::new();
        }
    };

    let body: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            warn!("debrid_cache federation parse [{service}]: {e}");
            return HashMap::new();
        }
    };

    body.get("cached_status")
        .and_then(|v| v.as_object())
        .map(|obj| {
            obj.iter()
                .map(|(k, v)| (k.clone(), v.as_bool().unwrap_or(false)))
                .collect()
        })
        .unwrap_or_default()
}

pub fn submit_federated_hashes(
    http: reqwest::Client,
    mediafusion_url: String,
    service: String,
    info_hashes: Vec<String>,
) {
    if info_hashes.is_empty() || mediafusion_url.is_empty() {
        return;
    }
    tokio::spawn(async move {
        let url = format!(
            "{}/streaming_provider/cache/submit",
            mediafusion_url.trim_end_matches('/')
        );
        if let Err(e) = http
            .post(&url)
            .json(&serde_json::json!({
                "service": service,
                "info_hashes": info_hashes,
            }))
            .send()
            .await
        {
            warn!("debrid_cache federation submit [{service}]: {e}");
        }
    });
}

pub async fn store_with_federation(
    redis: &RedisClient,
    http: &reqwest::Client,
    cache_service: &str,
    federation_service: &str,
    hashes: &[String],
    sync_federation: bool,
    mediafusion_url: &str,
) {
    if hashes.is_empty() {
        return;
    }
    let cache_key = format!("{CACHE_KEY_PREFIX}{cache_service}");
    let expiry_ts = (Utc::now().timestamp() + EXPIRY_DAYS_SECS).to_string();
    let pairs: Vec<(String, String)> = hashes
        .iter()
        .map(|h| (h.clone(), expiry_ts.clone()))
        .collect();
    if let Err(e) = redis.hset::<(), _, _>(&cache_key, pairs).await {
        warn!("debrid_cache store [{cache_key}]: {e}");
    }
    if sync_federation && !mediafusion_url.is_empty() {
        submit_federated_hashes(
            http.clone(),
            mediafusion_url.to_string(),
            federation_service.to_string(),
            hashes.to_vec(),
        );
    }
}

pub async fn cleanup_service_cache(redis: &RedisClient, service: &str) -> u64 {
    let cache_key = format!("{CACHE_KEY_PREFIX}{service}");
    let now = Utc::now().timestamp();
    let all: HashMap<String, String> = redis.hgetall(&cache_key).await.unwrap_or_default();
    let expired: Vec<String> = all
        .iter()
        .filter(|(_, ts)| ts.parse::<i64>().unwrap_or(0) <= now)
        .map(|(k, _)| k.clone())
        .collect();
    if expired.is_empty() {
        return 0;
    }
    if let Err(e) = redis.hdel::<(), _, _>(&cache_key, expired.clone()).await {
        warn!("debrid_cache cleanup HDEL [{cache_key}]: {e}");
        return 0;
    }
    info!(
        "debrid_cache cleanup [{service}]: removed {} expired entries",
        expired.len()
    );
    expired.len() as u64
}

pub async fn cleanup_all_services(redis: &RedisClient) -> u64 {
    let keys = scan_keys(redis, &format!("{CACHE_KEY_PREFIX}*")).await;
    let mut total = 0u64;
    for key in keys {
        let service = key.trim_start_matches(CACHE_KEY_PREFIX);
        total += cleanup_service_cache(redis, service).await;
    }
    total
}

async fn scan_keys(redis: &RedisClient, pattern: &str) -> Vec<String> {
    let mut all = Vec::new();
    let mut cursor = "0".to_string();
    loop {
        let result: Result<fred::types::Value, _> = redis
            .scan_page(cursor.clone(), pattern.to_string(), Some(500), None)
            .await;
        let (next, keys) = match result {
            Ok(v) => parse_scan(v),
            Err(e) => {
                warn!("debrid_cache scan [{pattern}]: {e}");
                break;
            }
        };
        all.extend(keys);
        if next == "0" {
            break;
        }
        cursor = next;
    }
    all
}

fn parse_scan(value: fred::types::Value) -> (String, Vec<String>) {
    if let fred::types::Value::Array(arr) = value {
        if arr.len() == 2 {
            let cursor = value_to_string(&arr[0]);
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

fn value_to_string(v: &fred::types::Value) -> String {
    match v {
        fred::types::Value::String(s) => s.to_string(),
        fred::types::Value::Bytes(b) => String::from_utf8_lossy(b).to_string(),
        fred::types::Value::Integer(n) => n.to_string(),
        other => format!("{other:?}"),
    }
}

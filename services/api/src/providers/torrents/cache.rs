/// Live debrid cache check dispatcher.
///
/// Each provider implements `check_cached` in its own module.
/// This module owns the Redis marker/storage layer and the public `live_check` entry point.
///
/// Marker key: `debrid_checked:{service}[:{user_hash}]:{media_id}` (30-min TTL)
/// Cache key:  `debrid_cache:{service}` → Redis hash of info_hash → Unix expiry timestamp
use std::collections::{HashMap, HashSet};

use chrono::Utc;
use fred::{clients::Client as RedisClient, prelude::*};
use sha2::{Digest, Sha256};
use tracing::warn;

const CACHE_KEY_PREFIX: &str = "debrid_cache:";
const CHECK_MARKER_PREFIX: &str = "debrid_checked:";
const EXPIRY_DAYS_SECS: i64 = 7 * 86400;
const CHECK_MARKER_TTL: i64 = 1800; // 30 minutes

/// Global providers check service-level CDN cache (same for all users).
const GLOBAL_CACHE_CHECK_PROVIDERS: &[&str] = &["torbox", "stremthru", "offcloud", "premiumize"];

// ─── Marker helpers ───────────────────────────────────────────────────────────

fn user_hash(token: &str) -> String {
    let digest = Sha256::digest(token.as_bytes());
    digest[..5].iter().map(|b| format!("{b:02x}")).collect()
}

fn check_marker_key(service: &str, token: &str, media_id: i32) -> String {
    if GLOBAL_CACHE_CHECK_PROVIDERS.contains(&service) {
        format!("{CHECK_MARKER_PREFIX}{service}:{media_id}")
    } else {
        format!(
            "{CHECK_MARKER_PREFIX}{service}:{}:{media_id}",
            user_hash(token)
        )
    }
}

pub async fn is_check_done(redis: &RedisClient, service: &str, token: &str, media_id: i32) -> bool {
    let key = check_marker_key(service, token, media_id);
    redis
        .exists::<i64, _>(&key)
        .await
        .map(|n| n > 0)
        .unwrap_or(false)
}

pub async fn mark_check_done(redis: &RedisClient, service: &str, token: &str, media_id: i32) {
    let key = check_marker_key(service, token, media_id);
    if let Err(e) = redis
        .set::<(), _, _>(
            &key,
            "1",
            Some(Expiration::EX(CHECK_MARKER_TTL)),
            None,
            false,
        )
        .await
    {
        warn!("debrid_cache mark_check_done [{key}]: {e}");
    }
}

// ─── Redis store ──────────────────────────────────────────────────────────────

pub async fn store_cached_hashes(redis: &RedisClient, service: &str, hashes: &[String]) {
    if hashes.is_empty() {
        return;
    }
    let cache_key = format!("{CACHE_KEY_PREFIX}{service}");
    let expiry_ts = (Utc::now().timestamp() + EXPIRY_DAYS_SECS).to_string();
    let pairs: Vec<(String, String)> = hashes
        .iter()
        .map(|h| (h.clone(), expiry_ts.clone()))
        .collect();
    let pairs_ref: Vec<(&str, &str)> = pairs
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    if let Err(e) = redis.hset::<(), _, _>(&cache_key, pairs_ref).await {
        warn!("debrid_cache store [{cache_key}]: {e}");
    }
}

// ─── Public dispatcher ────────────────────────────────────────────────────────

/// Check uncached hashes against the live provider API.
///
/// Delegates to each provider's own `check_cached` implementation.
/// Results are stored in Redis and the caller receives hash → cached map.
/// A 30-min marker prevents repeat calls within the same window.
pub async fn live_check(
    http: &reqwest::Client,
    redis: &RedisClient,
    service: &str,
    token: &str,
    hashes: &[String],
    media_id: i32,
) -> HashMap<String, bool> {
    if hashes.is_empty() || token.is_empty() {
        return HashMap::new();
    }

    if is_check_done(redis, service, token, media_id).await {
        return hashes.iter().map(|h| (h.clone(), false)).collect();
    }

    let newly_cached: Vec<String> = match service {
        "torbox" => super::torbox::check_cached(http, token, hashes).await,
        "premiumize" => super::premiumize::check_cached(http, token, hashes).await,
        "offcloud" => super::offcloud::check_cached(http, token, hashes).await,
        "easydebrid" => super::easydebrid::check_cached(http, token, hashes).await,
        "stremthru" => super::stremthru::check_cached(http, token, hashes, media_id).await,
        "alldebrid" => super::alldebrid::check_cached(http, token, hashes).await,
        "realdebrid" => super::realdebrid::check_cached(http, token, hashes).await,
        "debridlink" => super::debridlink::check_cached(http, token, hashes).await,
        "seedr" => super::seedr::check_cached(http, token, hashes).await,
        _ => {
            mark_check_done(redis, service, token, media_id).await;
            return hashes.iter().map(|h| (h.clone(), false)).collect();
        }
    };

    mark_check_done(redis, service, token, media_id).await;

    let cached_set: HashSet<String> = newly_cached.into_iter().map(|h| h.to_lowercase()).collect();
    if !cached_set.is_empty() {
        let to_store: Vec<String> = cached_set.iter().cloned().collect();
        store_cached_hashes(redis, service, &to_store).await;
    }

    hashes
        .iter()
        .map(|h| (h.clone(), cached_set.contains(&h.to_lowercase())))
        .collect()
}

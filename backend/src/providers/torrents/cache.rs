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

/// Providers that check cache by scanning the user's own download list rather than a
/// global CDN-hash endpoint. The full list is cached in Redis to prevent thundering-herd
/// when many streams are checked concurrently or when the watchlist catalog runs in parallel.
///
/// - realdebrid: paginates GET /torrents (up to 100 pages × 100 items)
/// - alldebrid:  single GET /magnet/status?status=ready (all user's ready magnets)
/// - seedr:      fetches root folder contents
const USER_LIST_CHECK_PROVIDERS: &[&str] = &["realdebrid", "alldebrid", "seedr"];
const USER_HASHES_PREFIX: &str = "user_hashes:";
const USER_HASHES_TTL: i64 = 300; // 5 minutes

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

// ─── User-account hash list cache ────────────────────────────────────────────

/// Return the full set of downloaded info-hashes for `service`/`token`, using a
/// short-lived Redis cache to avoid hammering the provider API.
///
/// Used by providers whose cache-check API IS the user's download list (no global
/// CDN endpoint). Shared between `live_check` and the watchlist catalog so both
/// code paths benefit from the same cache window.
pub async fn get_user_hashes_cached(
    http: &reqwest::Client,
    redis: &RedisClient,
    service: &str,
    token: &str,
) -> HashSet<String> {
    let key = format!("{USER_HASHES_PREFIX}{service}:{}", user_hash(token));

    if let Ok(Some(json)) = redis.get::<Option<String>, _>(&key).await {
        if let Ok(hashes) = serde_json::from_str::<Vec<String>>(&json) {
            return hashes.into_iter().collect();
        }
    }

    let hashes =
        match crate::providers::torrents::list_downloaded_hashes(http, service, token).await {
            Ok(h) => h,
            Err(e) => {
                // Auth / rate-limit failures are expected (token expired, quota hit).
                // Log at debug so they don't flood production logs; other errors stay warn.
                if matches!(
                    e.video_file(),
                    "invalid_token.mp4" | "too_many_requests.mp4"
                ) {
                    tracing::debug!("user_hashes [{service}]: {e}");
                } else {
                    warn!("user_hashes [{service}]: {e}");
                }
                return HashSet::new();
            }
        };

    if !hashes.is_empty() {
        match serde_json::to_string(&hashes) {
            Ok(json) => {
                if let Err(e) = redis
                    .set::<(), _, _>(
                        &key,
                        json,
                        Some(Expiration::EX(USER_HASHES_TTL)),
                        None,
                        false,
                    )
                    .await
                {
                    warn!("user_hashes set [{key}]: {e}");
                }
            }
            Err(e) => warn!("user_hashes serialize [{key}]: {e}"),
        }
    }

    hashes.into_iter().collect()
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

    let newly_cached: Vec<String> = if USER_LIST_CHECK_PROVIDERS.contains(&service) {
        // These providers have no global hash-check endpoint; cache check IS the user's
        // full download list. Use the shared Redis cache to avoid a fresh API fetch on
        // every concurrent media_id check within the same 5-minute window.
        let all = get_user_hashes_cached(http, redis, service, token).await;
        hashes
            .iter()
            .filter(|h| all.contains(h.to_lowercase().as_str()))
            .cloned()
            .collect()
    } else {
        match service {
            "torbox" => super::torbox::check_cached(http, token, hashes).await,
            "premiumize" => super::premiumize::check_cached(http, token, hashes).await,
            "offcloud" => super::offcloud::check_cached(http, token, hashes).await,
            "easydebrid" => super::easydebrid::check_cached(http, token, hashes).await,
            "stremthru" => super::stremthru::check_cached(http, token, hashes, media_id).await,
            "alldebrid" => super::alldebrid::check_cached(http, token, hashes).await,
            "debridlink" => super::debridlink::check_cached(http, token, hashes).await,
            _ => {
                mark_check_done(redis, service, token, media_id).await;
                return hashes.iter().map(|h| (h.clone(), false)).collect();
            }
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

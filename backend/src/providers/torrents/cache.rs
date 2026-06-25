/// Live debrid cache check dispatcher.
///
/// Each provider implements `check_cached` in its own module.
/// This module owns the Redis marker/storage layer and the public `live_check` entry point.
use std::collections::{HashMap, HashSet};

use chrono::Utc;
use fred::{clients::Client as RedisClient, prelude::*};
use sha2::{Digest, Sha256};
use tracing::warn;

use super::cache_federation;

const CACHE_KEY_PREFIX: &str = "debrid_cache:";
const CHECK_MARKER_PREFIX: &str = "debrid_checked:";
const EXPIRY_DAYS_SECS: i64 = 7 * 86400;
const CHECK_MARKER_TTL: i64 = 1800; // 30 minutes

/// Providers that check cache by scanning the user's own download list rather than a
/// global CDN-hash endpoint.
const USER_LIST_CHECK_PROVIDERS: &[&str] = &["realdebrid", "alldebrid", "seedr", "pikpak"];
const USER_HASHES_PREFIX: &str = "user_hashes:";
const USER_HASHES_TTL: i64 = 300; // 5 minutes

/// Global providers check service-level CDN cache (same for all users).
const GLOBAL_CACHE_CHECK_PROVIDERS: &[&str] =
    &["torbox", "stremthru", "offcloud", "premiumize", "torrin"];

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

pub async fn store_cached_hashes(redis: &RedisClient, cache_service: &str, hashes: &[String]) {
    store_cached_hashes_federated(redis, None, cache_service, cache_service, hashes, false, "")
        .await;
}

/// Store cached hashes locally and optionally submit to federated MediaFusion instance.
pub async fn store_cached_hashes_federated(
    redis: &RedisClient,
    http: Option<&reqwest::Client>,
    cache_service: &str,
    federation_service: &str,
    hashes: &[String],
    sync_federation: bool,
    mediafusion_url: &str,
) {
    if hashes.is_empty() {
        return;
    }
    if let Some(http) = http {
        cache_federation::store_with_federation(
            redis,
            http,
            cache_service,
            federation_service,
            hashes,
            sync_federation,
            mediafusion_url,
        )
        .await;
    } else {
        let cache_key = format!("{CACHE_KEY_PREFIX}{cache_service}");
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
}

/// Store cached hashes for a provider, honoring StremThru magnet-cache opt-in.
pub async fn store_cached_hashes_for_provider(
    redis: &RedisClient,
    http: Option<&reqwest::Client>,
    provider: &crate::models::user_data::StreamingProvider,
    hashes: &[String],
    sync_federation: bool,
    mediafusion_url: &str,
    store_stremthru_magnet_cache: bool,
) {
    if !cache_federation::should_store_magnet_cache(&provider.service, store_stremthru_magnet_cache)
    {
        return;
    }
    let cache_service = provider.cache_service_name();
    store_cached_hashes_federated(
        redis,
        http,
        &cache_service,
        &provider.service,
        hashes,
        sync_federation,
        mediafusion_url,
    )
    .await;
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

    if let Ok(Some(json)) = redis.get::<Option<String>, _>(&key).await
        && let Ok(hashes) = serde_json::from_str::<Vec<String>>(&json)
    {
        return hashes.into_iter().collect();
    }

    let hashes =
        match crate::providers::torrents::list_downloaded_hashes(http, service, token).await {
            Ok(h) => h,
            Err(e) => {
                e.log(&format!("user_hashes [{service}]"));
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
    provider_service: &str,
    cache_service: &str,
    token: &str,
    hashes: &[String],
    media_id: i32,
    store_stremthru_magnet_cache: bool,
) -> HashMap<String, bool> {
    if hashes.is_empty() || token.is_empty() {
        return HashMap::new();
    }

    if is_check_done(redis, cache_service, token, media_id).await {
        return hashes.iter().map(|h| (h.clone(), false)).collect();
    }

    let newly_cached: Vec<String> = if USER_LIST_CHECK_PROVIDERS.contains(&provider_service) {
        // These providers have no global hash-check endpoint; cache check IS the user's
        // full download list. Use the shared Redis cache to avoid a fresh API fetch on
        // every concurrent media_id check within the same 5-minute window.
        let all = get_user_hashes_cached(http, redis, provider_service, token).await;
        hashes
            .iter()
            .filter(|h| all.contains(h.to_lowercase().as_str()))
            .cloned()
            .collect()
    } else {
        match provider_service {
            "torbox" => super::torbox::check_cached(http, token, hashes).await,
            "premiumize" => super::premiumize::check_cached(http, token, hashes).await,
            "offcloud" => super::offcloud::check_cached(http, token, hashes).await,
            "easydebrid" => super::easydebrid::check_cached(http, token, hashes).await,
            "stremthru" => super::stremthru::check_cached(http, token, hashes, media_id).await,
            "torrin" => super::torrin::check_cached(http, token, hashes, media_id).await,
            "alldebrid" => super::alldebrid::check_cached(http, token, hashes).await,
            "debridlink" => super::debridlink::check_cached(http, token, hashes).await,
            "debrider" => super::debrider::check_cached(http, token, hashes, None).await,
            _ => {
                mark_check_done(redis, cache_service, token, media_id).await;
                return hashes.iter().map(|h| (h.clone(), false)).collect();
            }
        }
    };

    mark_check_done(redis, cache_service, token, media_id).await;

    let cached_set: HashSet<String> = newly_cached.into_iter().map(|h| h.to_lowercase()).collect();
    if !cached_set.is_empty()
        && cache_federation::should_store_magnet_cache(
            provider_service,
            store_stremthru_magnet_cache,
        )
    {
        let to_store: Vec<String> = cached_set.iter().cloned().collect();
        store_cached_hashes(redis, cache_service, &to_store).await;
    }

    hashes
        .iter()
        .map(|h| (h.clone(), cached_set.contains(&h.to_lowercase())))
        .collect()
}

//! BitTorrent tracker announce URLs — static bundle + dynamic best-trackers cache.

use std::sync::{OnceLock, RwLock};

use fred::prelude::{Expiration, KeysInterface};
use tracing::{debug, info, warn};

use crate::state::AppState;

const TRACKERS_CACHE_KEY: &str = "mediafusion:trackers:best:v1";
const TRACKERS_REFRESH_LOCK_KEY: &str = "mediafusion:trackers:refresh_lock";
const TRACKERS_CACHE_TTL: i64 = 60 * 60 * 24;
const TRACKERS_CACHE_WAIT_SECS: u64 = 5;
const BEST_TRACKERS_URL: &str =
    "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt";

static DEFAULT_TRACKERS: OnceLock<Vec<String>> = OnceLock::new();
static RUNTIME_TRACKERS: OnceLock<RwLock<Vec<String>>> = OnceLock::new();

fn bundled_trackers() -> Vec<String> {
    DEFAULT_TRACKERS
        .get_or_init(|| {
            serde_json::from_str(include_str!("../../../resources/json/trackers.json"))
                .unwrap_or_default()
        })
        .clone()
}

fn runtime_trackers() -> &'static RwLock<Vec<String>> {
    RUNTIME_TRACKERS.get_or_init(|| RwLock::new(bundled_trackers()))
}

/// Static tracker list bundled with the repo (Python `runtime_const.TRACKERS` seed).
pub fn default_trackers() -> &'static [String] {
    DEFAULT_TRACKERS
        .get_or_init(|| {
            serde_json::from_str(include_str!("../../../resources/json/trackers.json"))
                .unwrap_or_default()
        })
        .as_slice()
}

/// Merged runtime tracker list (bundled + Redis/upstream best trackers).
pub fn all_trackers() -> Vec<String> {
    runtime_trackers().read().unwrap().clone()
}

fn is_valid_tracker(url: &str) -> bool {
    url.starts_with("http://")
        || url.starts_with("https://")
        || url.starts_with("udp://")
        || url.starts_with("wss://")
}

fn merge_trackers(extra: &[String]) {
    let mut list = runtime_trackers().write().unwrap();
    for tracker in extra {
        if is_valid_tracker(tracker) && !list.iter().any(|t| t == tracker) {
            list.push(tracker.clone());
        }
    }
}

async fn cached_best_trackers(redis: &fred::clients::Client) -> Vec<String> {
    let raw: Option<String> = redis.get(TRACKERS_CACHE_KEY).await.ok().flatten();
    let Some(raw) = raw else {
        return Vec::new();
    };
    match serde_json::from_str::<Vec<String>>(&raw) {
        Ok(list) => list.into_iter().filter(|t| is_valid_tracker(t)).collect(),
        Err(e) => {
            warn!("Invalid best trackers payload in Redis cache: {e}");
            Vec::new()
        }
    }
}

/// Load best trackers from Redis cache and/or upstream list (Python `init_best_trackers`).
pub async fn init_best_trackers(state: &AppState) {
    if let Some(cached) = {
        let cached = cached_best_trackers(&state.redis).await;
        if cached.is_empty() {
            None
        } else {
            Some(cached)
        }
    } {
        merge_trackers(&cached);
        debug!(
            "Loaded {} trackers from Redis cache (total {})",
            cached.len(),
            all_trackers().len()
        );
        return;
    }

    let lock_acquired = state
        .redis
        .set::<bool, _, _>(
            TRACKERS_REFRESH_LOCK_KEY,
            true,
            Some(Expiration::EX(60)),
            None,
            true,
        )
        .await
        .unwrap_or(false);

    if !lock_acquired {
        // Another process (e.g. mediafusion-worker starting in parallel) is
        // already fetching. Poll for up to TRACKERS_CACHE_WAIT_SECS seconds.
        debug!(
            "Best tracker Redis lock held by another process; polling cache for up to {TRACKERS_CACHE_WAIT_SECS}s"
        );
        for _ in 0..TRACKERS_CACHE_WAIT_SECS {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            let cached = cached_best_trackers(&state.redis).await;
            if !cached.is_empty() {
                merge_trackers(&cached);
                debug!(
                    "Loaded {} trackers from Redis cache after wait (total {})",
                    cached.len(),
                    all_trackers().len()
                );
                return;
            }
        }
        // Lock holder may have crashed before populating the cache. Fall
        // through and fetch upstream directly rather than silently skipping.
        debug!("Best tracker cache still empty after wait; fetching upstream directly");
    }

    if let Some(cached) = {
        let cached = cached_best_trackers(&state.redis).await;
        if cached.is_empty() {
            None
        } else {
            Some(cached)
        }
    } {
        let _ = state.redis.del::<(), _>(TRACKERS_REFRESH_LOCK_KEY).await;
        merge_trackers(&cached);
        return;
    }

    let fetch_result = state
        .http
        .get(BEST_TRACKERS_URL)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await;

    let _ = state.redis.del::<(), _>(TRACKERS_REFRESH_LOCK_KEY).await;

    match fetch_result {
        Ok(resp) if resp.status().is_success() => {
            let text = resp.text().await.unwrap_or_default();
            let trackers: Vec<String> = text
                .lines()
                .map(str::trim)
                .filter(|l| !l.is_empty() && is_valid_tracker(l))
                .map(str::to_string)
                .collect();
            if !trackers.is_empty() {
                let payload = serde_json::to_string(&trackers).unwrap_or_default();
                let _ = state
                    .redis
                    .set::<(), _, _>(
                        TRACKERS_CACHE_KEY,
                        payload,
                        Some(Expiration::EX(TRACKERS_CACHE_TTL)),
                        None,
                        false,
                    )
                    .await;
                merge_trackers(&trackers);
                info!(
                    "Loaded {} best trackers from upstream (total {})",
                    trackers.len(),
                    all_trackers().len()
                );
            }
        }
        Ok(resp) => warn!("Failed to load best trackers: HTTP {}", resp.status()),
        Err(e) => warn!(
            error_kind = crate::util::http::transport_error_kind(&e),
            "Failed to fetch best trackers: {e}"
        ),
    }
}

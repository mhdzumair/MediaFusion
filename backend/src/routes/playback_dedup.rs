/// Deduplicate concurrent playback resolution (e.g. parallel player probes).
///
/// One request holds a short-lived Redis lock while resolving; peers poll the URL
/// cache until the holder finishes or the client wait budget expires.
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use fred::prelude::{Expiration, KeysInterface};
use tokio::task::JoinHandle;

use crate::providers::ProviderError;

/// Max time a waiter polls for a peer before returning an error video to the player.
pub const CLIENT_WAIT_BUDGET_SECS: u64 = 18;

/// Max time the lock holder may spend resolving a provider URL.
pub const HOLDER_RESOLVE_TIMEOUT_SECS: u64 = 90;

/// Lock TTL — extended by the holder while resolving.
pub const RESOLVE_LOCK_TTL_SECS: i64 = 30;

const LOCK_REFRESH_INTERVAL_SECS: u64 = 10;

/// Reclaim orphaned locks (crash/restart) when the acquire timestamp is this old.
const STALE_LOCK_SECS: u64 = 25;

const POLL_INTERVAL: Duration = Duration::from_millis(200);

const LOCK_SUFFIX: &str = ":locked";

pub fn holder_resolve_timeout() -> Duration {
    Duration::from_secs(HOLDER_RESOLVE_TIMEOUT_SECS)
}

pub fn playback_resolve_timed_out() -> ProviderError {
    ProviderError::api(
        "Playback resolution timed out. Please try again.",
        "torrent_not_downloaded.mp4",
    )
}

pub fn lock_key(cache_key: &str) -> String {
    format!("{cache_key}{LOCK_SUFFIX}")
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

async fn read_lock_value(redis: &fred::clients::Client, cache_key: &str) -> Option<String> {
    redis
        .get::<Option<String>, _>(&lock_key(cache_key))
        .await
        .ok()
        .flatten()
}

async fn lock_age_secs(redis: &fred::clients::Client, cache_key: &str) -> Option<u64> {
    let raw = read_lock_value(redis, cache_key).await?;
    let started = raw.parse::<u64>().ok()?;
    Some(now_secs().saturating_sub(started))
}

pub async fn try_acquire_lock(redis: &fred::clients::Client, cache_key: &str) -> bool {
    let started = now_secs().to_string();
    redis
        .set::<bool, _, _>(
            &lock_key(cache_key),
            started.as_str(),
            Some(Expiration::EX(RESOLVE_LOCK_TTL_SECS)),
            None,
            true,
        )
        .await
        .unwrap_or(false)
}

pub async fn release_lock(redis: &fred::clients::Client, cache_key: &str) {
    let _ = redis.del::<(), _>(&lock_key(cache_key)).await;
}

async fn refresh_lock_ttl(redis: &fred::clients::Client, cache_key: &str) {
    let _ = redis
        .expire::<(), _>(&lock_key(cache_key), RESOLVE_LOCK_TTL_SECS, None)
        .await;
}

async fn lock_held(redis: &fred::clients::Client, cache_key: &str) -> bool {
    redis
        .exists::<i64, _>(&lock_key(cache_key))
        .await
        .map(|n| n > 0)
        .unwrap_or(false)
}

async fn read_cache_value(redis: &fred::clients::Client, cache_key: &str) -> Option<String> {
    if let Ok(Some(cached)) = redis.get::<Option<Vec<u8>>, _>(cache_key).await {
        if let Ok(url) = String::from_utf8(cached) {
            if !url.is_empty() {
                return Some(url);
            }
        }
    }

    redis
        .get::<Option<String>, _>(cache_key)
        .await
        .ok()
        .flatten()
        .filter(|s| !s.is_empty())
}

/// Drop orphaned playback locks left after a crash/restart.
pub async fn reclaim_stale_lock(redis: &fred::clients::Client, cache_key: &str) -> bool {
    if read_cache_value(redis, cache_key).await.is_some() {
        return false;
    }

    let Some(raw) = read_lock_value(redis, cache_key).await else {
        return false;
    };

    let stale = match raw.parse::<u64>() {
        Ok(_) => lock_age_secs(redis, cache_key)
            .await
            .is_some_and(|age| age >= STALE_LOCK_SECS),
        Err(_) => true,
    };

    if stale {
        let age = lock_age_secs(redis, cache_key).await;
        tracing::warn!(
            cache_key = %cache_key,
            lock_value = %raw,
            lock_age_secs = ?age,
            "reclaiming stale playback resolve lock"
        );
        release_lock(redis, cache_key).await;
        return true;
    }

    false
}

/// Keeps the resolve lock TTL alive while provider work runs.
pub struct ResolveLockGuard {
    redis: fred::clients::Client,
    cache_key: String,
    refresh_task: Option<JoinHandle<()>>,
}

impl ResolveLockGuard {
    pub async fn acquire(redis: &fred::clients::Client, cache_key: &str) -> Option<Self> {
        if !try_acquire_lock(redis, cache_key).await {
            return None;
        }

        tracing::info!(cache_key = %cache_key, "acquired playback resolve lock");

        let redis_clone = redis.clone();
        let cache_key_owned = cache_key.to_string();
        let refresh_task = tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(LOCK_REFRESH_INTERVAL_SECS)).await;
                refresh_lock_ttl(&redis_clone, &cache_key_owned).await;
            }
        });

        Some(Self {
            redis: redis.clone(),
            cache_key: cache_key.to_string(),
            refresh_task: Some(refresh_task),
        })
    }

    pub async fn release(mut self) {
        if let Some(task) = self.refresh_task.take() {
            task.abort();
        }
        release_lock(&self.redis, &self.cache_key).await;
    }
}

pub enum DedupWaitResult {
    ReadyToResolve,
    Cached(String),
    TimedOut,
}

/// Check cache, reclaim stale locks, or wait for an in-flight peer to finish.
pub async fn prepare_resolve(
    redis: &fred::clients::Client,
    cache_key: &str,
) -> DedupWaitResult {
    if let Some(url) = read_cache_value(redis, cache_key).await {
        return DedupWaitResult::Cached(url);
    }

    let _ = reclaim_stale_lock(redis, cache_key).await;

    if !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    let age = lock_age_secs(redis, cache_key).await;
    tracing::debug!(
        cache_key = %cache_key,
        lock_age_secs = ?age,
        "playback lock held by another request; waiting for cache"
    );

    if let Some(url) =
        wait_for_cached_url(redis, cache_key, Duration::from_secs(CLIENT_WAIT_BUDGET_SECS)).await
    {
        return DedupWaitResult::Cached(url);
    }

    if reclaim_stale_lock(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    if !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    DedupWaitResult::TimedOut
}

/// Poll Redis until a cached URL appears or the lock is released.
pub async fn wait_for_cached_url(
    redis: &fred::clients::Client,
    cache_key: &str,
    max_wait: Duration,
) -> Option<String> {
    if max_wait.is_zero() {
        return read_cache_value(redis, cache_key).await;
    }

    let deadline = tokio::time::Instant::now() + max_wait;

    while tokio::time::Instant::now() < deadline {
        let sleep_for =
            POLL_INTERVAL.min(deadline.saturating_duration_since(tokio::time::Instant::now()));
        if sleep_for.is_zero() {
            break;
        }
        tokio::time::sleep(sleep_for).await;

        if let Some(url) = read_cache_value(redis, cache_key).await {
            return Some(url);
        }

        if !lock_held(redis, cache_key).await {
            tokio::time::sleep(Duration::from_millis(100)).await;
            return read_cache_value(redis, cache_key).await;
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lock_key_appends_suffix() {
        assert_eq!(lock_key("playback_url:abc"), "playback_url:abc:locked");
    }

    #[test]
    fn client_wait_fits_player_timeout() {
        const _: () = assert!(CLIENT_WAIT_BUDGET_SECS <= 20);
    }
}

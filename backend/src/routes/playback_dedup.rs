/// Deduplicate concurrent playback resolution (e.g. parallel HEAD + GET probes).
///
/// Matches Python `acquire_redis_lock(..., block=True)`: one request resolves while
/// peers wait for the cache. The lock is released when the holder finishes, times out,
/// or its HTTP connection is dropped (see `ResolveLockGuard::Drop`).
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use fred::prelude::{Expiration, KeysInterface, SetOptions};
use tokio::task::JoinHandle;

use crate::providers::ProviderError;

/// Max time waiters poll for a peer to finish (Python blocked on the lock until release).
pub const CLIENT_WAIT_BUDGET_SECS: u64 = 105;

/// Max time the lock holder may spend resolving a provider URL.
pub const HOLDER_RESOLVE_TIMEOUT_SECS: u64 = 90;

/// Browser streamability probes use HEAD with ~5s client timeout — cap server HEAD work so
/// the lock is released for the follow-up GET.
pub const HEAD_RESOLVE_BUDGET: Duration = Duration::from_secs(8);

/// Lock TTL — extended by the holder while resolving.
pub const RESOLVE_LOCK_TTL_SECS: i64 = 30;

const LOCK_REFRESH_INTERVAL_SECS: u64 = 10;

/// Reclaim orphaned locks only after the holder resolve budget (not while a live resolve runs).
const STALE_LOCK_SECS: u64 = HOLDER_RESOLVE_TIMEOUT_SECS;

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
    let result: Option<String> = redis
        .set(
            &lock_key(cache_key),
            started.as_str(),
            Some(Expiration::EX(RESOLVE_LOCK_TTL_SECS)),
            Some(SetOptions::NX),
            false,
        )
        .await
        .ok()
        .flatten();
    result.is_some()
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

/// Drop orphaned playback locks left after a crash/restart or a holder that exceeded its budget.
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
        tracing::debug!(
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
    released: AtomicBool,
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
            released: AtomicBool::new(false),
        })
    }

    pub async fn release(mut self) {
        if self.released.swap(true, Ordering::SeqCst) {
            return;
        }
        if let Some(task) = self.refresh_task.take() {
            task.abort();
        }
        release_lock(&self.redis, &self.cache_key).await;
    }
}

impl Drop for ResolveLockGuard {
    fn drop(&mut self) {
        if self.released.load(Ordering::SeqCst) {
            return;
        }
        if let Some(task) = self.refresh_task.take() {
            task.abort();
        }
        let redis = self.redis.clone();
        let cache_key = self.cache_key.clone();
        tracing::debug!(
            cache_key = %cache_key,
            "releasing playback resolve lock (client disconnect or task cancelled)"
        );
        tokio::spawn(async move {
            release_lock(&redis, &cache_key).await;
        });
    }
}

pub enum DedupWaitResult {
    ReadyToResolve,
    Cached(String),
    TimedOut,
}

/// Check cache, then wait for an in-flight peer (Python blocking lock) without stealing a young lock.
pub async fn prepare_resolve(redis: &fred::clients::Client, cache_key: &str) -> DedupWaitResult {
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

    if let Some(url) = wait_for_cached_url(
        redis,
        cache_key,
        Duration::from_secs(CLIENT_WAIT_BUDGET_SECS),
    )
    .await
    {
        return DedupWaitResult::Cached(url);
    }

    if let Some(url) = read_cache_value(redis, cache_key).await {
        return DedupWaitResult::Cached(url);
    }

    if !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    // Peer still resolving within budget — do not start a duplicate TorBox add.
    if lock_age_secs(redis, cache_key)
        .await
        .is_some_and(|age| age < STALE_LOCK_SECS)
    {
        return DedupWaitResult::TimedOut;
    }

    if reclaim_stale_lock(redis, cache_key).await && !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    if !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    DedupWaitResult::TimedOut
}

/// After a wait timeout, try cache once more; only become resolver if lock is free or truly stale.
pub async fn try_ready_after_wait(
    redis: &fred::clients::Client,
    cache_key: &str,
) -> DedupWaitResult {
    if let Some(url) = read_cache_value(redis, cache_key).await {
        return DedupWaitResult::Cached(url);
    }

    if !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    if lock_age_secs(redis, cache_key)
        .await
        .is_some_and(|age| age >= STALE_LOCK_SECS)
    {
        let _ = reclaim_stale_lock(redis, cache_key).await;
        if !lock_held(redis, cache_key).await {
            return DedupWaitResult::ReadyToResolve;
        }
    }

    DedupWaitResult::TimedOut
}

/// Acquire the resolve lock, retrying briefly when a peer just released it (HEAD disconnect).
pub async fn acquire_resolve_lock(
    redis: &fred::clients::Client,
    cache_key: &str,
) -> Option<ResolveLockGuard> {
    const RETRIES: usize = 10;
    for attempt in 0..RETRIES {
        if let Some(guard) = ResolveLockGuard::acquire(redis, cache_key).await {
            return Some(guard);
        }
        let _ = reclaim_stale_lock(redis, cache_key).await;
        if attempt + 1 < RETRIES {
            tokio::time::sleep(Duration::from_millis(150)).await;
        }
    }
    None
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
    fn stale_lock_not_before_holder_timeout() {
        const _: () = assert!(STALE_LOCK_SECS >= HOLDER_RESOLVE_TIMEOUT_SECS);
    }

    #[test]
    fn client_wait_covers_holder_resolve() {
        const _: () = assert!(CLIENT_WAIT_BUDGET_SECS >= HOLDER_RESOLVE_TIMEOUT_SECS);
    }
}

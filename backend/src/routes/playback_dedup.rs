/// Deduplicate concurrent playback resolution (e.g. parallel HEAD + GET probes).
///
/// Matches Python `acquire_redis_lock(..., block=True)`: one request resolves while
/// peers wait for the cache. The lock is released when the holder finishes, times out,
/// or its HTTP connection is dropped (see `ResolveLockGuard::Drop`).
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use fred::prelude::{Expiration, KeysInterface, LuaInterface, SetOptions};
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

const POLL_INTERVAL: Duration = Duration::from_millis(200);

const LOCK_SUFFIX: &str = ":locked";

/// A provider's resolution and peer-wait budgets must agree.
#[derive(Clone, Copy)]
pub struct ResolveBudget {
    pub holder: Duration,
}

impl Default for ResolveBudget {
    fn default() -> Self {
        Self {
            holder: Duration::from_secs(HOLDER_RESOLVE_TIMEOUT_SECS),
        }
    }
}

impl ResolveBudget {
    pub fn client_wait(self) -> Duration {
        self.holder.saturating_add(Duration::from_secs(
            CLIENT_WAIT_BUDGET_SECS - HOLDER_RESOLVE_TIMEOUT_SECS,
        ))
    }
}

// New locks record the holder's budget, so a request using a different profile
// timeout cannot reclaim a live long-running resolve. Numeric legacy locks keep
// the old 90-second budget until they expire.
fn lock_started(raw: &str) -> Option<u64> {
    raw.split(':').next()?.parse().ok()
}

fn lock_is_stale(raw: &str, now: u64) -> bool {
    let Some(started) = lock_started(raw) else {
        return true;
    };
    let timeout = match raw.split(':').nth(1) {
        Some(value) => match value.parse::<u64>() {
            Ok(n) => n,
            Err(_) => return true,
        },
        None => HOLDER_RESOLVE_TIMEOUT_SECS,
    };
    now.saturating_sub(started) >= timeout
}

const RELEASE_LOCK: &str =
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0";
const REFRESH_LOCK: &str = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) end return 0";

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
    let started = lock_started(&raw)?;
    Some(now_secs().saturating_sub(started))
}

async fn try_acquire_lock(
    redis: &fred::clients::Client,
    cache_key: &str,
    budget: ResolveBudget,
) -> Option<String> {
    let value = format!(
        "{}:{}:{}",
        now_secs(),
        budget.holder.as_secs().max(1),
        uuid::Uuid::new_v4()
    );
    let result: Option<String> = redis
        .set(
            &lock_key(cache_key),
            &value,
            Some(Expiration::EX(RESOLVE_LOCK_TTL_SECS)),
            Some(SetOptions::NX),
            false,
        )
        .await
        .ok()
        .flatten();
    result.map(|_| value)
}

async fn release_lock(redis: &fred::clients::Client, cache_key: &str, owner: &str) -> bool {
    redis
        .eval::<i64, _, _, _>(RELEASE_LOCK, lock_key(cache_key), owner)
        .await
        .is_ok_and(|deleted| deleted == 1)
}

async fn refresh_lock_ttl(
    redis: &fred::clients::Client,
    cache_key: &str,
    owner: &str,
) -> Result<bool, fred::error::Error> {
    redis
        .eval::<i64, _, _, _>(
            REFRESH_LOCK,
            lock_key(cache_key),
            (owner, RESOLVE_LOCK_TTL_SECS),
        )
        .await
        .map(|refreshed| refreshed == 1)
}

async fn lock_held(redis: &fred::clients::Client, cache_key: &str) -> bool {
    redis
        .exists::<i64, _>(&lock_key(cache_key))
        .await
        .map(|n| n > 0)
        .unwrap_or(false)
}

async fn read_cache_value(redis: &fred::clients::Client, cache_key: &str) -> Option<String> {
    if let Ok(Some(cached)) = redis.get::<Option<Vec<u8>>, _>(cache_key).await
        && let Ok(url) = String::from_utf8(cached)
        && !url.is_empty()
    {
        return Some(url);
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

    let stale = lock_is_stale(&raw, now_secs());

    if stale {
        let age = lock_age_secs(redis, cache_key).await;
        tracing::debug!(
            cache_key = %cache_key,
            lock_value = %raw,
            lock_age_secs = ?age,
            "reclaiming stale playback resolve lock"
        );
        return release_lock(redis, cache_key, &raw).await;
    }

    false
}

/// Keeps the resolve lock TTL alive while provider work runs.
pub struct ResolveLockGuard {
    redis: fred::clients::Client,
    cache_key: String,
    owner: String,
    refresh_task: Option<JoinHandle<()>>,
    released: AtomicBool,
}

impl ResolveLockGuard {
    pub async fn acquire(
        redis: &fred::clients::Client,
        cache_key: &str,
        budget: ResolveBudget,
    ) -> Option<Self> {
        let owner = try_acquire_lock(redis, cache_key, budget).await?;

        tracing::info!(cache_key = %cache_key, "acquired playback resolve lock");

        let redis_clone = redis.clone();
        let cache_key_owned = cache_key.to_string();
        let refresh_owner = owner.clone();
        let refresh_task = tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(LOCK_REFRESH_INTERVAL_SECS)).await;
                if matches!(
                    refresh_lock_ttl(&redis_clone, &cache_key_owned, &refresh_owner).await,
                    Ok(false)
                ) {
                    break;
                }
            }
        });

        Some(Self {
            redis: redis.clone(),
            cache_key: cache_key.to_string(),
            owner,
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
        release_lock(&self.redis, &self.cache_key, &self.owner).await;
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
        let owner = self.owner.clone();
        tracing::debug!(
            cache_key = %cache_key,
            "releasing playback resolve lock (client disconnect or task cancelled)"
        );
        tokio::spawn(async move {
            release_lock(&redis, &cache_key, &owner).await;
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
    prepare_resolve_with_budget(redis, cache_key, ResolveBudget::default()).await
}

pub async fn prepare_resolve_with_budget(
    redis: &fred::clients::Client,
    cache_key: &str,
    budget: ResolveBudget,
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

    if let Some(url) = wait_for_cached_url(redis, cache_key, budget.client_wait()).await {
        return DedupWaitResult::Cached(url);
    }

    try_ready_after_wait(redis, cache_key).await
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

    if reclaim_stale_lock(redis, cache_key).await && !lock_held(redis, cache_key).await {
        return DedupWaitResult::ReadyToResolve;
    }

    DedupWaitResult::TimedOut
}

/// Acquire the resolve lock, retrying briefly when a peer just released it (HEAD disconnect).
pub async fn acquire_resolve_lock(
    redis: &fred::clients::Client,
    cache_key: &str,
) -> Option<ResolveLockGuard> {
    acquire_resolve_lock_with_budget(redis, cache_key, ResolveBudget::default()).await
}

pub async fn acquire_resolve_lock_with_budget(
    redis: &fred::clients::Client,
    cache_key: &str,
    budget: ResolveBudget,
) -> Option<ResolveLockGuard> {
    const RETRIES: usize = 10;
    for attempt in 0..RETRIES {
        if let Some(guard) = ResolveLockGuard::acquire(redis, cache_key, budget).await {
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

    #[tokio::test(start_paused = true)]
    async fn long_provider_and_waiter_timeouts_cover_the_configured_download() {
        let budget = ResolveBudget {
            holder: Duration::from_secs(720),
        };
        assert_eq!(budget.client_wait(), Duration::from_secs(735));
        assert!(
            tokio::time::timeout(budget.holder, tokio::time::sleep(Duration::from_secs(300)))
                .await
                .is_ok()
        );
        assert!(
            tokio::time::timeout(budget.client_wait(), tokio::time::sleep(budget.holder))
                .await
                .is_ok()
        );
        assert_eq!(
            ResolveBudget::default().client_wait(),
            Duration::from_secs(105)
        );
    }

    async fn test_redis() -> fred::clients::Client {
        crate::cache::client::build(&std::env::var("TEST_REDIS_URL").expect("set TEST_REDIS_URL"))
            .await
            .unwrap()
    }

    #[tokio::test]
    #[ignore = "requires TEST_REDIS_URL"]
    async fn a_long_running_holder_survives_ninety_seconds_and_hands_off_its_cache() {
        let redis = test_redis().await;
        let key = format!("test:playback:{}", uuid::Uuid::new_v4());
        let raw = format!("{}:720:owner", now_secs() - 120);
        redis
            .set::<(), _, _>(lock_key(&key), raw, Some(Expiration::EX(30)), None, false)
            .await
            .unwrap();
        assert!(!reclaim_stale_lock(&redis, &key).await);
        assert!(matches!(
            try_ready_after_wait(&redis, &key).await,
            DedupWaitResult::TimedOut
        ));
        assert!(
            try_acquire_lock(&redis, &key, ResolveBudget::default())
                .await
                .is_none()
        );
        let publish = async {
            tokio::time::sleep(Duration::from_millis(100)).await;
            redis
                .set::<(), _, _>(
                    &key,
                    "https://dav/video.mkv",
                    Some(Expiration::EX(30)),
                    None,
                    false,
                )
                .await
                .unwrap();
        };
        let (result, ()) = tokio::join!(
            prepare_resolve_with_budget(
                &redis,
                &key,
                ResolveBudget {
                    holder: Duration::from_secs(720)
                }
            ),
            publish
        );
        assert!(matches!(result, DedupWaitResult::Cached(url) if url == "https://dav/video.mkv"));
        redis
            .del::<i64, _>(vec![key.clone(), lock_key(&key)])
            .await
            .unwrap();
    }

    #[tokio::test]
    #[ignore = "requires TEST_REDIS_URL"]
    async fn stale_and_legacy_locks_are_reclaimed_after_their_own_budgets() {
        let redis = test_redis().await;
        let key = format!("test:playback:{}", uuid::Uuid::new_v4());
        for value in [
            format!("{}:720:owner", now_secs() - 721),
            (now_secs() - 91).to_string(),
            "invalid".into(),
        ] {
            redis
                .set::<(), _, _>(lock_key(&key), value, Some(Expiration::EX(30)), None, false)
                .await
                .unwrap();
            assert!(reclaim_stale_lock(&redis, &key).await);
            assert!(!lock_held(&redis, &key).await);
        }
    }

    #[tokio::test]
    #[ignore = "requires TEST_REDIS_URL"]
    async fn cancelled_and_superseded_guards_release_only_their_own_lock() {
        let redis = test_redis().await;
        let key = format!("test:playback:{}", uuid::Uuid::new_v4());
        let guard = acquire_resolve_lock_with_budget(
            &redis,
            &key,
            ResolveBudget {
                holder: Duration::from_secs(720),
            },
        )
        .await
        .unwrap();
        assert!(!lock_is_stale(&guard.owner, now_secs() + 120));
        let old_owner = guard.owner.clone();
        redis
            .set::<(), _, _>(
                lock_key(&key),
                "replacement",
                Some(Expiration::EX(30)),
                None,
                false,
            )
            .await
            .unwrap();
        assert!(!refresh_lock_ttl(&redis, &key, &old_owner).await.unwrap());
        guard.release().await;
        assert_eq!(
            read_lock_value(&redis, &key).await.as_deref(),
            Some("replacement")
        );
        redis.del::<i64, _>(lock_key(&key)).await.unwrap();
        let guard = acquire_resolve_lock(&redis, &key).await.unwrap();
        drop(guard);
        tokio::time::timeout(Duration::from_secs(2), async {
            while lock_held(&redis, &key).await {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
    }

    #[test]
    fn lock_key_appends_suffix() {
        assert_eq!(lock_key("playback_url:abc"), "playback_url:abc:locked");
    }

    #[test]
    fn stale_lock_not_before_holder_timeout() {
        assert!(!lock_is_stale("1000:720:owner", 1091));
        assert!(!lock_is_stale("1000:720:owner", 1719));
        assert!(lock_is_stale("1000:720:owner", 1720));
        assert!(!lock_is_stale("1000", 1089));
        assert!(lock_is_stale("1000", 1090));
    }

    #[test]
    fn client_wait_covers_holder_resolve() {
        const _: () = assert!(CLIENT_WAIT_BUDGET_SECS >= HOLDER_RESOLVE_TIMEOUT_SECS);
    }
}

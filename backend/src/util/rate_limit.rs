use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

use governor::{DefaultDirectRateLimiter, Quota, RateLimiter};
use moka::sync::Cache;
use once_cell::sync::Lazy;

type Limiter = Arc<DefaultDirectRateLimiter>;

// TTL of 1 hour: limiters for domains not recently scraped are evicted automatically.
static LIMITERS: Lazy<Cache<String, Limiter>> = Lazy::new(|| {
    Cache::builder()
        .max_capacity(1_024)
        .time_to_idle(Duration::from_secs(3600))
        .build()
});

/// Get-or-create a rate limiter for `key` allowing `rps` requests per second.
pub async fn get_limiter(key: &str, rps: u32) -> Limiter {
    if let Some(l) = LIMITERS.get(key) {
        return l;
    }
    let quota = Quota::per_second(NonZeroU32::new(rps.max(1)).unwrap());
    let limiter = Arc::new(RateLimiter::direct(quota));
    LIMITERS.insert(key.to_string(), Arc::clone(&limiter));
    limiter
}

/// Wait until the rate limiter grants a token (async, no spin).
pub async fn wait(key: &str, rps: u32) {
    let limiter = get_limiter(key, rps).await;
    limiter.until_ready().await;
}

/// Get-or-create a rate limiter capped at `rpm` requests per minute.
pub async fn get_limiter_rpm(key: &str, rpm: u32) -> Limiter {
    let rpm_key = format!("{key}::rpm");
    if let Some(l) = LIMITERS.get(&rpm_key) {
        return l;
    }
    let quota = Quota::per_minute(NonZeroU32::new(rpm.max(1)).unwrap());
    let limiter = Arc::new(RateLimiter::direct(quota));
    LIMITERS.insert(rpm_key, Arc::clone(&limiter));
    limiter
}

/// Wait until the rate limiter grants a token (rpm-based, async, no spin).
pub async fn wait_rpm(key: &str, rpm: u32) {
    let limiter = get_limiter_rpm(key, rpm).await;
    limiter.until_ready().await;
}

/// Extract a domain from a URL for use as a limiter key.
pub fn domain_key(url: &str) -> String {
    url::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_string()))
        .unwrap_or_else(|| url.to_string())
}

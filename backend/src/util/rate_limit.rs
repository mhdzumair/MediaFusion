use std::collections::HashMap;
use std::num::NonZeroU32;
use std::sync::Arc;

use governor::{DefaultDirectRateLimiter, Quota, RateLimiter};
use once_cell::sync::Lazy;
use tokio::sync::RwLock;

type Limiter = Arc<DefaultDirectRateLimiter>;

static LIMITERS: Lazy<RwLock<HashMap<String, Limiter>>> = Lazy::new(|| RwLock::new(HashMap::new()));

/// Get-or-create a rate limiter for `key` allowing `rps` requests per second.
pub async fn get_limiter(key: &str, rps: u32) -> Limiter {
    {
        let guard = LIMITERS.read().await;
        if let Some(l) = guard.get(key) {
            return Arc::clone(l);
        }
    }
    let mut guard = LIMITERS.write().await;
    guard
        .entry(key.to_string())
        .or_insert_with(|| {
            let quota = Quota::per_second(NonZeroU32::new(rps.max(1)).unwrap());
            Arc::new(RateLimiter::direct(quota))
        })
        .clone()
}

/// Wait until the rate limiter grants a token (async, no spin).
pub async fn wait(key: &str, rps: u32) {
    let limiter = get_limiter(key, rps).await;
    limiter.until_ready().await;
}

/// Get-or-create a rate limiter capped at `rpm` requests per minute.
pub async fn get_limiter_rpm(key: &str, rpm: u32) -> Limiter {
    let rpm_key = format!("{key}::rpm");
    {
        let guard = LIMITERS.read().await;
        if let Some(l) = guard.get(&rpm_key) {
            return Arc::clone(l);
        }
    }
    let mut guard = LIMITERS.write().await;
    guard
        .entry(rpm_key)
        .or_insert_with(|| {
            let quota = Quota::per_minute(NonZeroU32::new(rpm.max(1)).unwrap());
            Arc::new(RateLimiter::direct(quota))
        })
        .clone()
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

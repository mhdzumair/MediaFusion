use std::time::Duration;

use backon::{ExponentialBuilder, Retryable};
use tracing::{debug, warn};

/// Default retry policy for HTML scraper calls.
/// Up to `max_times` attempts, exponential backoff starting at 2s, capped at 60s.
pub fn scrape_retry() -> ExponentialBuilder {
    ExponentialBuilder::default()
        .with_min_delay(Duration::from_secs(2))
        .with_max_delay(Duration::from_secs(60))
        .with_max_times(4)
        .with_jitter()
}

/// Latency-bounded retry policy for user-facing API calls.
/// Up to 3 retries (4 total attempts), short backoff. The extra attempt covers HTTP/2
/// connection-pool scenarios where multiple stale connections can fail in sequence before
/// a fresh one is established (GOAWAY/RST_STREAM appear as Kind::Request errors).
pub fn api_retry() -> ExponentialBuilder {
    ExponentialBuilder::default()
        .with_min_delay(Duration::from_millis(200))
        .with_max_delay(Duration::from_secs(2))
        .with_max_times(3)
        .with_jitter()
}

/// Execute `f` with the default scraping retry policy.
/// Logs a warning on each transient failure.
pub async fn with_retry<F, Fut, T, E>(label: &str, f: F) -> Result<T, E>
where
    F: Fn() -> Fut + Send,
    Fut: std::future::Future<Output = Result<T, E>> + Send,
    E: std::fmt::Display,
{
    let policy = scrape_retry();
    f.retry(policy)
        .notify(|err: &E, dur: Duration| {
            warn!(label, delay_ms = dur.as_millis(), "retrying after: {err}");
        })
        .await
}

/// Execute `f` with the API retry policy, retrying **only** transport-level errors
/// (connect, timeout, request send). Never retries 4xx/5xx HTTP status responses.
///
/// A single transient failure is expected and unremarkable (logged at `debug`).
/// Only escalate to `warn` once every attempt — including the final one — has
/// failed, since that's the case that actually needs attention.
pub async fn with_transport_retry<F, Fut, T>(label: &str, f: F) -> Result<T, reqwest::Error>
where
    F: Fn() -> Fut + Send,
    Fut: std::future::Future<Output = Result<T, reqwest::Error>> + Send,
{
    let policy = api_retry();
    let result = f
        .retry(policy)
        .when(|e: &reqwest::Error| crate::util::http::is_transport_error(e))
        .notify(|err: &reqwest::Error, dur: Duration| {
            let kind = crate::util::http::transport_error_kind(err);
            let root = crate::util::http::root_cause(err);
            debug!(
                "retrying transport error [{label}]: kind={kind}, root=\"{root}\", delay={}ms — {err}",
                dur.as_millis()
            );
        })
        .await;

    if let Err(err) = &result {
        let kind = crate::util::http::transport_error_kind(err);
        let root = crate::util::http::root_cause(err);
        warn!(
            "transport error [{label}] failed after all retries: kind={kind}, root=\"{root}\" — {err}"
        );
    }
    result
}

use std::time::Duration;

use backon::{ExponentialBuilder, Retryable};
use tracing::warn;

/// Default retry policy for HTTP scraping calls.
/// Up to `max_times` attempts, exponential backoff starting at 2s, capped at 60s.
pub fn scrape_retry() -> ExponentialBuilder {
    ExponentialBuilder::default()
        .with_min_delay(Duration::from_secs(2))
        .with_max_delay(Duration::from_secs(60))
        .with_max_times(4)
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

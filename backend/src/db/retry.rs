use std::time::Duration;

use backon::{ExponentialBuilder, Retryable};
use tracing::warn;

/// Returns `true` for transient Postgres conditions that are safe to retry:
/// - SQLSTATE 57014 (`query_canceled`): hot-standby recovery conflict — the replica's WAL
///   replay cancelled this read statement. Re-running usually succeeds once replay advances.
/// - SQLSTATE 40001 (`serialization_failure`): repeatable-read / serializable transaction
///   race; the standard retry target for multi-version concurrency control.
pub fn is_retryable(e: &sqlx::Error) -> bool {
    if let sqlx::Error::Database(dbe) = e {
        matches!(dbe.code().as_deref(), Some("57014") | Some("40001"))
    } else {
        false
    }
}

/// Short retry policy for transient DB conditions (recovery conflicts, serialization failures).
/// Fast backoff so read-replica conflicts resolve before noticeable user delay.
pub fn db_retry() -> ExponentialBuilder {
    ExponentialBuilder::default()
        .with_min_delay(Duration::from_millis(100))
        .with_max_delay(Duration::from_secs(1))
        .with_max_times(3)
        .with_jitter()
}

/// Execute `f` with the DB retry policy, retrying only on recoverable transient errors.
/// After all retries are exhausted the last error is returned to the caller.
pub async fn with_retry<F, Fut, T>(label: &str, f: F) -> Result<T, sqlx::Error>
where
    F: Fn() -> Fut + Send,
    Fut: std::future::Future<Output = Result<T, sqlx::Error>> + Send,
{
    let policy = db_retry();
    f.retry(policy)
        .when(|e: &sqlx::Error| is_retryable(e))
        .notify(|err: &sqlx::Error, dur: Duration| {
            warn!(
                label,
                delay_ms = dur.as_millis(),
                "retrying transient DB error: {err}"
            );
        })
        .await
}

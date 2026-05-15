use serde::Serialize;
use sqlx::PgConnection;

use super::error::JobError;

#[derive(Debug, Default)]
pub struct EnqueueOpts {
    pub priority: Option<i16>,
    pub max_attempts: Option<i32>,
    /// Dedupe key — if another pending/running job with same key exists, this insert is silently skipped.
    pub dedupe_key: Option<String>,
    /// Delay before the job becomes runnable.
    pub delay_secs: Option<i64>,
}

/// Enqueue a job within an existing transaction. Returns the new job id, or
/// `None` if the insert was skipped due to a conflicting `dedupe_key`.
pub async fn enqueue<P: Serialize>(
    conn: &mut PgConnection,
    queue: &str,
    payload: &P,
    opts: EnqueueOpts,
) -> Result<Option<i64>, JobError> {
    let payload_json = serde_json::to_value(payload)?;
    let priority = opts.priority.unwrap_or(100);
    let max_attempts = opts.max_attempts.unwrap_or(5);

    let row = sqlx::query!(
        r#"
        INSERT INTO jobs (queue, payload, priority, max_attempts, dedupe_key,
                          scheduled_at)
        VALUES ($1, $2, $3, $4, $5,
                CASE WHEN $6::bigint IS NOT NULL
                     THEN now() + ($6 * interval '1 second')
                     ELSE now()
                END)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id
        "#,
        queue,
        payload_json,
        priority,
        max_attempts,
        opts.dedupe_key,
        opts.delay_secs,
    )
    .fetch_optional(conn)
    .await?;

    Ok(row.map(|r| r.id))
}

/// Convenience: enqueue without an explicit transaction (uses a pool).
pub async fn enqueue_simple<P: Serialize>(
    pool: &sqlx::PgPool,
    queue: &str,
    payload: &P,
    opts: EnqueueOpts,
) -> Result<Option<i64>, JobError> {
    let mut conn = pool.acquire().await?;
    enqueue(&mut conn, queue, payload, opts).await
}

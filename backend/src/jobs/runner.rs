use std::sync::Arc;
use std::time::{Duration, Instant};

use sqlx::{PgPool, postgres::PgListener};
use tokio::sync::Semaphore;
use tokio::time::sleep;
use tokio_util::sync::CancellationToken;
use tracing::{debug, error, info, warn};
use uuid::Uuid;

use super::{
    error::JobError,
    handler::{ErasedHandler, JobCtx},
    metrics::JobMetrics,
};
use crate::state::AppState;

const POLL_FALLBACK_SECS: u64 = 2;
const IDLE_CLAIM_TIMEOUT_MINS: i64 = 30;

struct ClaimedJob {
    id: i64,
    payload: serde_json::Value,
    attempts: i32,
    cancel_requested: bool,
}

pub struct QueueRunner {
    queue: &'static str,
    handler: Arc<dyn ErasedHandler>,
    semaphore: Arc<Semaphore>,
    state: Arc<AppState>,
    metrics: Arc<JobMetrics>,
    worker_id: String,
    cancel: CancellationToken,
}

impl QueueRunner {
    pub fn new(
        handler: Arc<dyn ErasedHandler>,
        state: Arc<AppState>,
        metrics: Arc<JobMetrics>,
        cancel: CancellationToken,
    ) -> Self {
        let queue = handler.queue();
        let concurrency = handler.concurrency();
        Self {
            queue,
            handler,
            semaphore: Arc::new(Semaphore::new(concurrency)),
            state,
            metrics,
            worker_id: format!("{}-{}", queue, Uuid::new_v4().simple()),
            cancel,
        }
    }

    pub fn start(self) {
        tokio::spawn(async move { self.run_loop().await });
    }

    async fn run_loop(self) {
        info!(queue = self.queue, "runner started");

        // Reclaim idle jobs that were running when a previous worker crashed.
        self.reclaim_stale_jobs().await;

        let pool = self.state.pool.clone();
        // Use a dedicated standalone connection for LISTEN/NOTIFY so it is never
        // returned to the shared pool (which would cause NotificationResponse errors
        // on unrelated query connections).
        let mut listener = match PgListener::connect(&self.state.config.postgres_uri).await {
            Ok(l) => l,
            Err(e) => {
                error!(queue = self.queue, "PgListener connect failed: {e}");
                return;
            }
        };
        let channel = format!("jobs_new_{}", self.queue);
        if let Err(e) = listener.listen(&channel).await {
            error!(queue = self.queue, "LISTEN failed: {e}");
            return;
        }

        let cancel_channel = "job_cancel";
        if let Err(e) = listener.listen(cancel_channel).await {
            warn!(queue = self.queue, "LISTEN job_cancel failed: {e}");
        }

        loop {
            tokio::select! {
                _ = self.cancel.cancelled() => {
                    info!(queue = self.queue, "runner shutting down");
                    break;
                }
                notif = listener.recv() => {
                    match notif {
                        Ok(n) if n.channel() == cancel_channel => {
                            if let Ok(job_id) = n.payload().parse::<i64>() {
                                self.handle_cancel_notification(job_id);
                            }
                        }
                        Ok(_) => {}  // jobs_new_{queue} — fall through to claim
                        Err(e) => warn!(queue = self.queue, "listener error: {e}"),
                    }
                    self.claim_and_dispatch(&pool).await;
                }
                _ = sleep(Duration::from_secs(POLL_FALLBACK_SECS)) => {
                    self.claim_and_dispatch(&pool).await;
                }
            }
        }
    }

    async fn claim_and_dispatch(&self, pool: &PgPool) {
        let available = self.semaphore.available_permits();
        if available == 0 {
            return;
        }

        let jobs = match self.claim_jobs(pool, available as i64).await {
            Ok(j) => j,
            Err(e) => {
                warn!(queue = self.queue, "claim error: {e}");
                return;
            }
        };

        for job in jobs {
            let permit = match self.semaphore.clone().acquire_owned().await {
                Ok(p) => p,
                Err(_) => break,
            };

            let handler = Arc::clone(&self.handler);
            let state = Arc::clone(&self.state);
            let metrics = Arc::clone(&self.metrics);
            let pool = pool.to_owned();
            let worker_id = self.worker_id.clone();
            let queue = self.queue;

            tokio::spawn(async move {
                let job_cancel = CancellationToken::new();
                if job.cancel_requested {
                    job_cancel.cancel();
                }
                // Store cancel token so notification handler can fire it.
                // (stored in a global map keyed by job_id — see cancel_tokens module)
                super::cancel_tokens::register(job.id, job_cancel.clone());

                let ctx = JobCtx {
                    job_id: job.id,
                    attempt: job.attempts,
                    state: Arc::clone(&state),
                    cancel: job_cancel.clone(),
                };

                let start = Instant::now();
                let result = handler.run_erased(job.payload, ctx).await;
                let elapsed = start.elapsed();

                super::cancel_tokens::deregister(job.id);

                match result {
                    Ok(()) => {
                        metrics.record_outcome(queue, "success", elapsed);
                        Self::mark_success(&pool, job.id).await;
                    }
                    Err(JobError::Cancelled) => {
                        metrics.record_outcome(queue, "cancelled", elapsed);
                        Self::mark_cancelled(&pool, job.id).await;
                    }
                    Err(e) => {
                        warn!(
                            queue,
                            job_id = job.id,
                            attempt = job.attempts,
                            "job failed: {e}"
                        );
                        metrics.record_outcome(queue, "error", elapsed);
                        Self::mark_error(&pool, job.id, &worker_id, &e.to_string()).await;
                    }
                }

                drop(permit);
            });
        }
    }

    async fn claim_jobs(&self, pool: &PgPool, limit: i64) -> Result<Vec<ClaimedJob>, sqlx::Error> {
        let rows = sqlx::query!(
            r#"
            UPDATE jobs
               SET status    = 'running',
                   started_at = now(),
                   worker_id  = $1,
                   attempts   = attempts + 1
             WHERE id IN (
                   SELECT id FROM jobs
                    WHERE queue      = $2
                      AND status     = 'pending'
                      AND scheduled_at <= now()
                    ORDER BY priority, scheduled_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $3
                   )
            RETURNING id, payload, attempts, cancel_requested
            "#,
            self.worker_id,
            self.queue,
            limit,
        )
        .fetch_all(pool)
        .await?;

        Ok(rows
            .into_iter()
            .map(|r| ClaimedJob {
                id: r.id,
                payload: r.payload,
                attempts: r.attempts,
                cancel_requested: r.cancel_requested,
            })
            .collect())
    }

    async fn reclaim_stale_jobs(&self) {
        // Reset jobs that were running on this queue when the last worker crashed.
        match sqlx::query!(
            r#"
            UPDATE jobs SET status = 'pending', started_at = NULL, worker_id = NULL
            WHERE queue = $1
              AND status = 'running'
              AND started_at < now() - ($2 * interval '1 minute')
            "#,
            self.queue,
            IDLE_CLAIM_TIMEOUT_MINS as f64,
        )
        .execute(&self.state.pool)
        .await
        {
            Ok(r) if r.rows_affected() > 0 => {
                info!(
                    queue = self.queue,
                    reclaimed = r.rows_affected(),
                    "reclaimed stale jobs"
                );
            }
            Err(e) => warn!(queue = self.queue, "reclaim error: {e}"),
            _ => {}
        }
    }

    fn handle_cancel_notification(&self, job_id: i64) {
        if let Some(token) = super::cancel_tokens::get(job_id) {
            debug!(job_id, "cancellation received");
            token.cancel();
        }
    }

    async fn mark_success(pool: &PgPool, job_id: i64) {
        let _ = sqlx::query!(
            "UPDATE jobs SET status='success', finished_at=now() WHERE id=$1",
            job_id
        )
        .execute(pool)
        .await;
        Self::write_event(pool, job_id, "success", None).await;
    }

    async fn mark_cancelled(pool: &PgPool, job_id: i64) {
        let _ = sqlx::query!(
            "UPDATE jobs SET status='cancelled', finished_at=now() WHERE id=$1",
            job_id
        )
        .execute(pool)
        .await;
        Self::write_event(pool, job_id, "cancelled", None).await;
    }

    async fn mark_error(pool: &PgPool, job_id: i64, _worker_id: &str, error: &str) {
        // Increment attempt count was already done in the claim. Check max_attempts.
        let row = sqlx::query!(
            "SELECT attempts, max_attempts FROM jobs WHERE id=$1",
            job_id
        )
        .fetch_optional(pool)
        .await;

        let (attempts, max_attempts) = match row {
            Ok(Some(r)) => (r.attempts, r.max_attempts),
            _ => {
                return;
            }
        };

        if attempts >= max_attempts {
            let _ = sqlx::query!(
                "UPDATE jobs SET status='dead', finished_at=now(), last_error=$1 WHERE id=$2",
                error,
                job_id
            )
            .execute(pool)
            .await;
            Self::write_event(
                pool,
                job_id,
                "dead",
                Some(serde_json::json!({"error": error})),
            )
            .await;
            warn!(job_id, "job dead after {attempts} attempts");
        } else {
            // Exponential backoff: 30s * 2^(attempts-1), capped at 1h, plus jitter
            let base_secs: i64 = 30 * (1_i64 << (attempts - 1).min(6));
            let jitter: i64 = rand::random::<u8>() as i64 % 10;
            let backoff_secs = base_secs.min(3600) + jitter;
            let _ = sqlx::query!(
                r#"
                UPDATE jobs
                   SET status = 'pending',
                       scheduled_at = now() + ($1 * interval '1 second'),
                       last_error = $2,
                       worker_id = NULL
                 WHERE id = $3
                "#,
                backoff_secs as f64,
                error,
                job_id
            )
            .execute(pool)
            .await;
            Self::write_event(
                pool,
                job_id,
                "retry",
                Some(serde_json::json!({
                    "error": error,
                    "retry_in_secs": backoff_secs,
                    "attempts": attempts,
                })),
            )
            .await;
        }
    }

    async fn write_event(
        pool: &PgPool,
        job_id: i64,
        event: &str,
        detail: Option<serde_json::Value>,
    ) {
        job_write_event(pool, job_id, event, detail).await;
    }
}

/// Insert a `running` job row for inline CLI runs (`--run-job`).
pub(crate) async fn insert_inline_job(
    pool: &PgPool,
    queue: &str,
    payload: &serde_json::Value,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO jobs (queue, payload, status, priority, attempts, max_attempts, started_at, worker_id)
        VALUES ($1, $2, 'running', 100, 1, 1, now(), 'inline-cli')
        RETURNING id
        "#,
    )
    .bind(queue)
    .bind(payload)
    .fetch_one(pool)
    .await
}

pub(crate) async fn mark_job_success(pool: &PgPool, job_id: i64) {
    let _ = sqlx::query!(
        "UPDATE jobs SET status='success', finished_at=now() WHERE id=$1",
        job_id
    )
    .execute(pool)
    .await;
    job_write_event(pool, job_id, "success", None).await;
}

pub(crate) async fn mark_job_cancelled(pool: &PgPool, job_id: i64) {
    let _ = sqlx::query!(
        "UPDATE jobs SET status='cancelled', finished_at=now() WHERE id=$1",
        job_id
    )
    .execute(pool)
    .await;
    job_write_event(pool, job_id, "cancelled", None).await;
}

/// Mark an inline job failed (no retries — max_attempts is always 1).
pub(crate) async fn mark_job_failed(pool: &PgPool, job_id: i64, error: &str) {
    let _ = sqlx::query!(
        "UPDATE jobs SET status='dead', finished_at=now(), last_error=$1 WHERE id=$2",
        error,
        job_id
    )
    .execute(pool)
    .await;
    job_write_event(
        pool,
        job_id,
        "dead",
        Some(serde_json::json!({"error": error})),
    )
    .await;
}

async fn job_write_event(
    pool: &PgPool,
    job_id: i64,
    event: &str,
    detail: Option<serde_json::Value>,
) {
    let _ = sqlx::query!(
        "INSERT INTO job_events (job_id, event, detail) VALUES ($1, $2, $3)",
        job_id,
        event,
        detail
    )
    .execute(pool)
    .await;
}

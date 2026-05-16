use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use cron::Schedule;
use sqlx::pool::PoolConnection;
use sqlx::{PgPool, Postgres};
use tokio::time::sleep;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use super::enqueue::{enqueue_simple, EnqueueOpts};

const ADVISORY_LOCK_KEY: i64 = 0x6D666A6F62; // "mfjob"
const SCHEDULER_TICK_SECS: u64 = 1;
const LOCK_RETRY_SECS: u64 = 30;

pub async fn run(pool: Arc<PgPool>, cancel: CancellationToken) {
    loop {
        tokio::select! {
            _ = cancel.cancelled() => { info!("scheduler stopping"); return; }
            _ = sleep(Duration::from_secs(LOCK_RETRY_SECS)) => {}
            result = try_acquire_lock(&pool) => {
                match result {
                    Some(conn) => {
                        info!("scheduler: acquired advisory lock, taking cron ownership");
                        // Hold the dedicated connection alive — dropping it releases the lock.
                        tick_loop(&pool, conn, &cancel).await;
                        info!("scheduler: released advisory lock");
                    }
                    None => {
                        // Lock held by another replica; try again after LOCK_RETRY_SECS.
                    }
                }
            }
        }
    }
}

/// Attempt to acquire the advisory lock on a **dedicated** connection.
/// Returns the connection (keeping the lock alive) on success, None on failure.
async fn try_acquire_lock(pool: &PgPool) -> Option<PoolConnection<Postgres>> {
    let mut conn = pool.acquire().await.ok()?;
    let acquired: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock($1)")
        .bind(ADVISORY_LOCK_KEY)
        .fetch_one(&mut *conn)
        .await
        .unwrap_or(false);
    if acquired {
        Some(conn)
    } else {
        None
    }
}

async fn tick_loop(
    pool: &PgPool,
    mut lock_conn: PoolConnection<Postgres>,
    cancel: &CancellationToken,
) {
    let mut interval = tokio::time::interval(Duration::from_secs(SCHEDULER_TICK_SECS));
    loop {
        tokio::select! {
            _ = cancel.cancelled() => break,
            _ = interval.tick() => {
                if let Err(e) = tick_once(pool).await {
                    warn!("scheduler tick error: {e}");
                }
                // Verify lock is still on this connection (pool recycling guard).
                if !still_holds_lock(&mut lock_conn).await { break; }
            }
        }
    }
    // Explicit unlock — only if the connection still owns the lock.
    // If still_holds_lock() already returned false the backend PID changed
    // (connection recycled), meaning the lock is already gone; calling
    // pg_advisory_unlock on a different PID produces a noisy warning.
    if still_holds_lock(&mut lock_conn).await {
        let _ = sqlx::query("SELECT pg_advisory_unlock($1)")
            .bind(ADVISORY_LOCK_KEY)
            .execute(&mut *lock_conn)
            .await;
    }
}

async fn still_holds_lock(conn: &mut PoolConnection<Postgres>) -> bool {
    sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(
           SELECT 1 FROM pg_locks
           WHERE locktype='advisory'
             AND classid=($1>>32)::int
             AND objid=($1 & x'ffffffff'::bigint)::int
             AND pid=pg_backend_pid()
         )",
    )
    .bind(ADVISORY_LOCK_KEY)
    .fetch_one(&mut **conn)
    .await
    .unwrap_or(false)
}

async fn tick_once(pool: &PgPool) -> Result<(), sqlx::Error> {
    let now = Utc::now();
    let jobs = sqlx::query!(
        "SELECT name, schedule, queue, payload, last_enqueued_at
         FROM cron_jobs
         WHERE enabled = true"
    )
    .fetch_all(pool)
    .await?;

    for job in jobs {
        let schedule = match to_cron_schedule(&job.schedule) {
            Ok(s) => s,
            Err(e) => {
                warn!(
                    name = job.name,
                    schedule = job.schedule,
                    "invalid cron: {e}"
                );
                continue;
            }
        };

        let last = job.last_enqueued_at.unwrap_or(chrono::DateTime::UNIX_EPOCH);
        let Some(next) = schedule.after(&last).next() else {
            continue;
        };

        if next > now {
            continue;
        }

        // Dedupe key uses a minute-bucket so rapid restarts don't double-fire.
        let minute_bucket = now.format("%Y%m%d%H%M");
        let dedupe_key = format!("cron:{}:{}", job.name, minute_bucket);

        debug!(name = job.name, queue = job.queue, "firing cron job");

        let enqueued = enqueue_simple(
            pool,
            &job.queue,
            &job.payload,
            EnqueueOpts {
                dedupe_key: Some(dedupe_key),
                ..Default::default()
            },
        )
        .await;

        match enqueued {
            Ok(Some(id)) => {
                debug!(name = job.name, job_id = id, "enqueued");
                let _ = sqlx::query!(
                    "UPDATE cron_jobs SET last_enqueued_at = now() WHERE name = $1",
                    job.name
                )
                .execute(pool)
                .await;
            }
            Ok(None) => {
                debug!(name = job.name, "skipped (dedupe)");
            }
            Err(e) => warn!(name = job.name, "enqueue error: {e}"),
        }
    }

    Ok(())
}

fn to_cron_schedule(five_field: &str) -> Result<Schedule, cron::error::Error> {
    let seven_field = format!("0 {} *", five_field);
    Schedule::from_str(&seven_field)
}

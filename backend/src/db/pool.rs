use sqlx::{postgres::PgPoolOptions, PgPool};
use std::time::Duration;

/// Tuning knobs for a single PgPool instance.
/// All durations come from [`crate::config::AppConfig`] which reads them from env vars.
#[derive(Clone)]
pub struct PoolConfig {
    /// Hard cap on open connections (`DB_POOL_SIZE` / `DB_POOL_SIZE_RO`).
    pub max_connections: u32,
    /// Minimum idle connections kept warm (`DB_POOL_MIN`, default 2).
    pub min_connections: u32,
    /// How long a checkout waits before returning `PoolTimedOut` (`DB_ACQUIRE_TIMEOUT_SECS`, default 5).
    pub acquire_timeout_secs: u64,
    /// Drop idle connections older than this (`DB_IDLE_TIMEOUT_SECS`, default 600).
    pub idle_timeout_secs: u64,
    /// Recycle connections older than this; ensures DNS/endpoint re-resolution after a
    /// failover (`DB_MAX_LIFETIME_SECS`, default 1800).
    pub max_lifetime_secs: u64,
    /// Per-session `statement_timeout` in milliseconds (`DB_STATEMENT_TIMEOUT_MS`, default
    /// 60 000). Applied via `after_connect` so it takes effect regardless of how the
    /// connection was created. Matches the DB-side guard the hoster applies.
    pub statement_timeout_ms: u64,
    /// Per-session `idle_in_transaction_session_timeout` in milliseconds
    /// (`DB_IDLE_TX_TIMEOUT_MS`, default 60 000). Bounds any leaked transaction so a
    /// single stalled request cannot hold row locks for hours.
    pub idle_in_transaction_timeout_ms: u64,
}

impl Default for PoolConfig {
    fn default() -> Self {
        Self {
            max_connections: 10,
            min_connections: 2,
            acquire_timeout_secs: 5,
            idle_timeout_secs: 600,
            max_lifetime_secs: 1800,
            statement_timeout_ms: 60_000,
            idle_in_transaction_timeout_ms: 60_000,
        }
    }
}

pub async fn build(uri: &str, cfg: PoolConfig) -> Result<PgPool, sqlx::Error> {
    let stmt_ms = cfg.statement_timeout_ms;
    let idle_ms = cfg.idle_in_transaction_timeout_ms;

    PgPoolOptions::new()
        .max_connections(cfg.max_connections)
        .min_connections(cfg.min_connections)
        .acquire_timeout(Duration::from_secs(cfg.acquire_timeout_secs))
        .idle_timeout(Duration::from_secs(cfg.idle_timeout_secs))
        .max_lifetime(Duration::from_secs(cfg.max_lifetime_secs))
        .after_connect(move |conn, _meta| {
            Box::pin(async move {
                // SET commands cannot use bind parameters in PostgreSQL; format the
                // integer millisecond values directly. These are trusted config values,
                // never user input.
                sqlx::query(&format!("SET statement_timeout = {stmt_ms}"))
                    .execute(&mut *conn)
                    .await?;
                sqlx::query(&format!(
                    "SET idle_in_transaction_session_timeout = {idle_ms}"
                ))
                .execute(&mut *conn)
                .await?;
                Ok(())
            })
        })
        .connect(uri)
        .await
}

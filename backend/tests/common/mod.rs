use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;
use tokio::sync::{Mutex, OnceCell};

static TEST_POOL: OnceCell<PgPool> = OnceCell::const_new();
static DB_TEST_LOCK: OnceCell<Mutex<()>> = OnceCell::const_new();

/// Serialize integration tests that share one Postgres database.
#[allow(dead_code)]
pub async fn lock_db_tests() -> tokio::sync::MutexGuard<'static, ()> {
    DB_TEST_LOCK
        .get_or_init(|| async { Mutex::new(()) })
        .await
        .lock()
        .await
}

/// Shared Postgres pool for integration tests (one pool per test binary).
///
/// Limits connections so parallel `#[tokio::test]` cases do not exhaust Postgres
/// or deadlock when cleaning up.
#[allow(dead_code)]
pub async fn test_pool() -> &'static PgPool {
    TEST_POOL
        .get_or_init(|| async {
            let url = std::env::var("TEST_DATABASE_URL").unwrap_or_else(|_| {
                "postgresql://mediafusion:mediafusion@127.0.0.1:5432/mediafusion".into()
            });
            PgPoolOptions::new()
                .max_connections(1)
                .acquire_timeout(std::time::Duration::from_secs(30))
                .connect(&url)
                .await
                .expect("test DB connect failed")
        })
        .await
}

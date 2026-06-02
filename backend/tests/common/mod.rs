use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;
use tokio::sync::OnceCell;

static TEST_POOL: OnceCell<PgPool> = OnceCell::const_new();

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
                .max_connections(4)
                .connect(&url)
                .await
                .expect("test DB connect failed")
        })
        .await
}
